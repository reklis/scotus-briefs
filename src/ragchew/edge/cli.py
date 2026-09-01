"""Long-running Raspberry Pi capture forwarding process."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import httpx

from ragchew.config import MvpConfig
from ragchew.edge.capture import load_finalized_call
from ragchew.edge.health import build_heartbeat, send_heartbeat
from ragchew.edge.spool import EdgeSpool, SpoolFullError
from ragchew.edge.uploader import EdgeUploader

LOG = logging.getLogger("ragchew.edge")


def main() -> None:
    logging.basicConfig(level=os.getenv("RAGCHEW_LOG_LEVEL", "INFO"))
    config = MvpConfig.from_yaml(os.getenv("RAGCHEW_CONFIG_PATH", "/etc/ragchew/mvp.yaml"))
    capture_dir = Path(os.getenv("RAGCHEW_EDGE_CAPTURE_DIR", "/var/lib/ragchew/capture"))
    spool_dir = Path(os.getenv("RAGCHEW_EDGE_SPOOL_DIR", "/var/lib/ragchew/spool"))
    metrics_path = Path(os.getenv("RAGCHEW_EDGE_RADIO_METRICS", "/run/ragchew/radio.json"))
    base_url = os.environ["RAGCHEW_INGESTION_URL"]
    token = os.environ["RAGCHEW_RECEIVER_TOKEN"]

    spool = EdgeSpool(spool_dir, config.receiver.spool_max_bytes)
    spool.recover_uploads()
    client = httpx.Client(timeout=30)
    uploader = EdgeUploader(
        base_url, config.receiver.receiver_id, token, spool, config, client
    )
    seen_metadata: set[Path] = set()
    next_heartbeat = 0.0

    try:
        while True:
            for metadata_path in capture_dir.rglob("*.json"):
                if metadata_path in seen_metadata:
                    continue
                try:
                    loaded = load_finalized_call(metadata_path, config)
                    if loaded is None:
                        continue
                    envelope, audio_path = loaded
                    spool.add(envelope, audio_path)
                    seen_metadata.add(metadata_path)
                except SpoolFullError:
                    LOG.exception("edge spool is full; capture remains in source directory")
                    break
                except (OSError, ValueError):
                    LOG.exception("failed to finalize %s", metadata_path)

            while uploader.process_one():
                pass
            spool.cleanup_acknowledged(config.receiver.acknowledged_grace_seconds)

            if time.monotonic() >= next_heartbeat:
                try:
                    heartbeat = build_heartbeat(
                        spool,
                        config,
                        disk_path=spool_dir,
                        metrics_path=metrics_path,
                    )
                    send_heartbeat(heartbeat, base_url, token, client)
                except (httpx.HTTPError, OSError):
                    LOG.exception("heartbeat delivery failed")
                next_heartbeat = time.monotonic() + config.receiver.heartbeat_seconds
            time.sleep(2)
    finally:
        client.close()
        spool.close()
