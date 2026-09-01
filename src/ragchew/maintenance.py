"""Retention maintenance entrypoint."""

from __future__ import annotations

import json

from ragchew.config import MvpConfig, ServiceSettings
from ragchew.repository import PostgresRepository
from ragchew.retention import RetentionService
from ragchew.storage import S3ObjectStore


def main() -> None:
    settings = ServiceSettings()
    config = MvpConfig.from_yaml(settings.config_path)
    result = RetentionService(
        PostgresRepository(settings.database_dsn), S3ObjectStore(settings), config
    ).run()
    print(json.dumps(result, sort_keys=True))
