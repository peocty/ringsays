"""Object storage for verification evidence.

- local: files under `settings.blob_dir` (MOCK object storage, refused in production)
- gcs:   Google Cloud Storage through the regional endpoint (Dammam: storage.me-central2.rep.googleapis.com),
         workload identity, bucket encrypted with a customer managed key (Terraform)
- s3:    S3 compatible bucket in Kingdom (Oracle, Alibaba, AWS), workload credentials from the platform

Keys are random, never derived from file names. Buckets are private; nothing is ever public or signed
for download: evidence is streamed through the audited back office endpoint.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any, Protocol

from app.core.config import settings


class BlobStore(Protocol):
    def put(self, data: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalBlobStore:
    """MOCK object storage on local disk."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        if not key.isalnum():
            raise ValueError("bad blob key")
        return self.root / key[:2] / key

    def put(self, data: bytes) -> str:
        key = secrets.token_hex(20)
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def _new_key() -> str:
    return secrets.token_hex(20)


def _check_key(key: str) -> str:
    if not key.isalnum():
        raise ValueError("bad blob key")
    return key


class GcsBlobStore:
    """Google Cloud Storage. `bucket` is a google.cloud.storage Bucket (or a test double)."""

    def __init__(self, bucket: Any, prefix: str = "") -> None:
        self.bucket = bucket
        self.prefix = prefix

    @classmethod
    def from_settings(cls) -> GcsBlobStore:
        from google.cloud import storage

        options = {"api_endpoint": settings.blob_endpoint} if settings.blob_endpoint else None
        client = storage.Client(client_options=options)
        return cls(client.bucket(settings.blob_bucket), settings.blob_prefix)

    def put(self, data: bytes) -> str:
        key = _new_key()
        # if_generation_match=0: create only, never overwrite an existing object.
        self.bucket.blob(self.prefix + key).upload_from_string(
            data, content_type="application/octet-stream", if_generation_match=0
        )
        return key

    def get(self, key: str) -> bytes:
        return bytes(self.bucket.blob(self.prefix + _check_key(key)).download_as_bytes())

    def delete(self, key: str) -> None:
        blob = self.bucket.blob(self.prefix + _check_key(key))
        try:
            blob.delete()
        except Exception as exc:  # already gone is fine; anything else surfaces
            if type(exc).__name__ != "NotFound":
                raise


class S3BlobStore:
    """S3 compatible object storage. `client` is a boto3 S3 client (or a test double)."""

    def __init__(self, client: Any, bucket: str, prefix: str = "") -> None:
        self.client = client
        self.bucket = bucket
        self.prefix = prefix

    @classmethod
    def from_settings(cls) -> S3BlobStore:
        import boto3

        client = boto3.client("s3", endpoint_url=settings.blob_endpoint, region_name=settings.blob_region)
        return cls(client, settings.blob_bucket, settings.blob_prefix)

    def put(self, data: bytes) -> str:
        key = _new_key()
        # Encryption at rest is the bucket's default (customer managed key), set by infrastructure.
        self.client.put_object(
            Bucket=self.bucket, Key=self.prefix + key, Body=data, ContentType="application/octet-stream"
        )
        return key

    def get(self, key: str) -> bytes:
        r = self.client.get_object(Bucket=self.bucket, Key=self.prefix + _check_key(key))
        return bytes(r["Body"].read())

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self.prefix + _check_key(key))


_store: BlobStore | None = None


def get_store() -> BlobStore:
    global _store
    if _store is None:
        if settings.blob_backend == "gcs":
            _store = GcsBlobStore.from_settings()
        elif settings.blob_backend == "s3":
            _store = S3BlobStore.from_settings()
        elif settings.blob_backend == "local":
            _store = LocalBlobStore(settings.blob_dir)
        else:
            raise RuntimeError(f"unknown RINGSAYS_BLOB_BACKEND {settings.blob_backend!r}")
    return _store


def set_store(store: BlobStore | None) -> None:
    global _store
    _store = store


def sniff_content_type(data: bytes) -> str | None:
    """Type from file content, not from the client's claim."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None
