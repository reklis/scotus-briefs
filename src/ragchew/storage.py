"""Private S3-compatible object storage adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Protocol

import boto3  # type: ignore[import-untyped]
from botocore.client import BaseClient  # type: ignore[import-untyped]

from ragchew.config import ServiceSettings


@dataclass(frozen=True)
class ObjectMetadata:
    byte_count: int
    content_type: str
    sha256: str | None


class ObjectStore(Protocol):
    def create_upload(self, key: str, content_type: str, sha256: str) -> str: ...

    def head(self, key: str) -> ObjectMetadata: ...

    def create_download(self, key: str, expires_seconds: int = 300) -> str: ...

    def put_file(
        self, key: str, file: BinaryIO, content_type: str, sha256: str
    ) -> None: ...

    def delete(self, key: str) -> None: ...


class S3ObjectStore:
    """Stores source material in a non-public S3 bucket."""

    def __init__(self, settings: ServiceSettings, client: BaseClient | None = None) -> None:
        self.bucket = settings.s3_bucket
        self.client = client or boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        )

    def create_upload(self, key: str, content_type: str, sha256: str) -> str:
        return str(
            self.client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    "ContentType": content_type,
                    "Metadata": {"sha256": sha256},
                },
                ExpiresIn=900,
            )
        )

    def head(self, key: str) -> ObjectMetadata:
        result = self.client.head_object(Bucket=self.bucket, Key=key)
        metadata = result.get("Metadata", {})
        return ObjectMetadata(
            byte_count=int(result["ContentLength"]),
            content_type=str(result.get("ContentType", "application/octet-stream")),
            sha256=metadata.get("sha256"),
        )

    def create_download(self, key: str, expires_seconds: int = 300) -> str:
        return str(
            self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_seconds,
            )
        )

    def put_file(
        self, key: str, file: BinaryIO, content_type: str, sha256: str
    ) -> None:
        file.seek(0)
        self.client.upload_fileobj(
            file,
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type, "Metadata": {"sha256": sha256}},
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
