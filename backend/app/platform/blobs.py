"""Object storage for verification evidence.

Local: files under `settings.blob_dir` (MOCK object storage). Production: an S3 compatible bucket in the
residency region with server side encryption and no public access; implement `BlobStore` for it.
Keys are random, never derived from file names.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Protocol

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


_store: BlobStore | None = None


def get_store() -> BlobStore:
    global _store
    if _store is None:
        _store = LocalBlobStore(settings.blob_dir)
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
