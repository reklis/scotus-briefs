"""Edge health collection and heartbeat delivery."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ragchew import __version__
from ragchew.config import MvpConfig
from ragchew.contracts import EdgeHeartbeat
from ragchew.edge.spool import EdgeSpool


def _cpu_temperature() -> float | None:
    path = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        return float(path.read_text().strip()) / 1000
    except (OSError, ValueError):
        return None


def _clock_offset() -> float | None:
    try:
        result = subprocess.run(
            ["timedatectl", "show", "--property=NTPOffsetUSec", "--value"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        raw = result.stdout.strip().removesuffix("us")
        return float(raw) / 1_000_000 if raw else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def load_radio_metrics(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def build_heartbeat(
    spool: EdgeSpool,
    config: MvpConfig,
    *,
    disk_path: Path,
    metrics_path: Path | None = None,
    now: datetime | None = None,
) -> EdgeHeartbeat:
    current = now or datetime.now(UTC)
    depth, oldest_age = spool.stats(current)
    disk = shutil.disk_usage(disk_path)
    radio = load_radio_metrics(metrics_path)
    return EdgeHeartbeat(
        receiver_id=config.receiver.receiver_id,
        observed_at=current,
        software_version=__version__,
        config_version=config.version,
        rf_min_hz=config.receiver.rf_min_hz,
        rf_max_hz=config.receiver.rf_max_hz,
        control_messages_per_minute=float(radio.get("control_messages_per_minute", 0)),
        last_finalized_call_at=radio.get("last_finalized_call_at"),
        last_acknowledged_call_at=radio.get("last_acknowledged_call_at"),
        spool_depth=depth,
        oldest_spool_age_seconds=oldest_age,
        free_disk_bytes=disk.free,
        dropped_samples=radio.get("dropped_samples"),
        clock_offset_seconds=_clock_offset(),
        cpu_temperature_c=_cpu_temperature(),
        out_of_range_calls=int(radio.get("out_of_range_calls", 0)),
    )


def send_heartbeat(
    heartbeat: EdgeHeartbeat,
    base_url: str,
    token: str,
    client: httpx.Client,
) -> None:
    response = client.post(
        f"{base_url.rstrip('/')}/v1/receivers/{heartbeat.receiver_id}/heartbeats",
        headers={"Authorization": f"Bearer {token}"},
        json=heartbeat.model_dump(mode="json"),
    )
    response.raise_for_status()
