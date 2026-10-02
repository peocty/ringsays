"""Outbox publisher against a real NATS JetStream server (skipped when none on 127.0.0.1:4222)."""

from __future__ import annotations

import asyncio
import json
import socket
import uuid

import pytest

from app.platform.outbox import STREAM, NatsPublisher

URL = "nats://127.0.0.1:4222"


def _up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 4222), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _up(), reason="no NATS server on 127.0.0.1:4222")


async def _count_and_last(subject: str) -> tuple[int, bytes]:
    import nats

    nc = await nats.connect(URL)
    try:
        js = nc.jetstream()
        info = await js.stream_info(STREAM, subjects_filter=subject)
        assert info.config.subjects == ["ringsays.>"]
        last = await js.get_last_msg(STREAM, subject)
        return (info.state.subjects or {}).get(subject, 0), last.data or b""
    finally:
        await nc.close()


def test_creates_stream_publishes_and_drops_duplicate_ids() -> None:
    subject = f"ringsays.test{uuid.uuid4().hex[:8]}.intent.created"
    run = uuid.uuid4().hex  # ids are deduplicated server side for 15 minutes, across runs too
    p = NatsPublisher(URL)
    try:
        p.publish(subject, json.dumps({"n": 1}).encode(), msg_id=f"{run}-1")
        p.publish(subject, json.dumps({"n": 1}).encode(), msg_id=f"{run}-1")  # relay retry
        p.publish(subject, json.dumps({"n": 2}).encode(), msg_id=f"{run}-2")
    finally:
        p.close()
    count, last = asyncio.run(_count_and_last(subject))
    assert count == 2, "duplicate outbox id must be dropped by the server"
    assert json.loads(last)["n"] == 2


def test_existing_stream_is_reused() -> None:
    for _ in range(2):
        p = NatsPublisher(URL)
        try:
            p.publish(f"ringsays.test{uuid.uuid4().hex[:8]}.x.y", b"{}")
        finally:
            p.close()
