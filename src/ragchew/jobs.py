"""Durable worker lease helpers."""

from __future__ import annotations

from dataclasses import dataclass

from ragchew.config import MvpConfig
from ragchew.repository import JobRecord, Repository


@dataclass
class JobLease:
    repository: Repository
    worker_id: str
    record: JobRecord
    config: MvpConfig
    completed: bool = False

    def complete(self, output_id: str) -> None:
        if not self.repository.complete_job(self.record.job_id, self.worker_id, output_id):
            raise RuntimeError("job lease was lost before completion")
        self.completed = True

    def fail(self, error: Exception) -> None:
        exponent = max(0, self.record.attempts - 1)
        delay = min(
            self.config.retry.maximum_delay_seconds,
            self.config.retry.base_delay_seconds * (2**exponent),
        )
        if not self.repository.fail_job(
            self.record.job_id,
            self.worker_id,
            str(error),
            self.config.retry.maximum_attempts,
            delay,
        ):
            raise RuntimeError("job lease was lost before failure handling")


def claim(
    repository: Repository,
    worker_id: str,
    config: MvpConfig,
    *,
    stages: tuple[str, ...] | None = None,
) -> JobLease | None:
    record = repository.claim_job(
        worker_id, config.retry.job_lease_seconds, stages=stages
    )
    return JobLease(repository, worker_id, record, config) if record else None
