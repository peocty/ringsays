"""Object storage backends: same behaviour for local, GCS and S3 compatible stores."""

from __future__ import annotations

import pytest

from app.platform.blobs import GcsBlobStore, LocalBlobStore, S3BlobStore


class FakeGcsBlob:
    def __init__(self, store: dict[str, bytes], name: str) -> None:
        self.store, self.name = store, name

    def upload_from_string(self, data: bytes, content_type: str, if_generation_match: int) -> None:
        assert if_generation_match == 0
        if self.name in self.store:
            raise RuntimeError("PreconditionFailed")
        self.store[self.name] = data

    def download_as_bytes(self) -> bytes:
        return self.store[self.name]

    def delete(self) -> None:
        if self.name not in self.store:
            raise type("NotFound", (Exception,), {})()
        del self.store[self.name]


class FakeGcsBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def blob(self, name: str) -> FakeGcsBlob:
        return FakeGcsBlob(self.objects, name)


def _roundtrip(store: object) -> None:
    key = store.put(b"%PDF-1.7 evidence")  # type: ignore[attr-defined]
    assert key.isalnum() and len(key) == 40
    assert store.get(key) == b"%PDF-1.7 evidence"  # type: ignore[attr-defined]
    store.delete(key)  # type: ignore[attr-defined]
    store.delete(key)  # type: ignore[attr-defined]  # already gone: fine
    with pytest.raises(ValueError):
        store.get("../etc/passwd")  # type: ignore[attr-defined]


def test_local(tmp_path) -> None:  # type: ignore[no-untyped-def]
    _roundtrip(LocalBlobStore(tmp_path))


def test_gcs_prefix_and_no_overwrite() -> None:
    bucket = FakeGcsBucket()
    store = GcsBlobStore(bucket, "evidence/")
    key = store.put(b"x")
    assert list(bucket.objects) == [f"evidence/{key}"]
    _roundtrip(store)


def test_s3_compatible() -> None:
    import boto3
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="me-central-1")
        client.create_bucket(
            Bucket="ringsays-evidence", CreateBucketConfiguration={"LocationConstraint": "me-central-1"}
        )
        store = S3BlobStore(client, "ringsays-evidence", "evidence/")
        key = store.put(b"y")
        names = [o["Key"] for o in client.list_objects_v2(Bucket="ringsays-evidence")["Contents"]]
        assert names == [f"evidence/{key}"]
        _roundtrip(store)


def test_gcs_uses_regional_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.cloud import storage

    from app.core.config import settings

    seen: dict[str, object] = {}

    class Client:
        def __init__(self, client_options: dict[str, str] | None = None) -> None:
            seen["options"] = client_options

        def bucket(self, name: str) -> FakeGcsBucket:
            seen["bucket"] = name
            return FakeGcsBucket()

    monkeypatch.setattr(storage, "Client", Client)
    monkeypatch.setattr(settings, "blob_bucket", "ringsays-prod-evidence")
    monkeypatch.setattr(settings, "blob_endpoint", "https://storage.me-central2.rep.googleapis.com")
    store = GcsBlobStore.from_settings()
    assert seen == {
        "options": {"api_endpoint": "https://storage.me-central2.rep.googleapis.com"},
        "bucket": "ringsays-prod-evidence",
    }
    assert store.prefix == settings.blob_prefix
