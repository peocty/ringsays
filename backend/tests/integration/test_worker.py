from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import worker
from app.modules.webhooks.service import MockHttpSender
from app.platform.outbox import MockPublisher
from app.scripts.seed import SeededTenant

from .conftest import Clock, auth, intent_body, token_for

pytestmark = pytest.mark.integration


def test_worker_tick_runs_every_job(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    tok = token_for(client, tenant)
    client.post(
        "/v1/intents", json=intent_body(tenant, channel_preference=["PSTN"]), headers=auth(tok, str(uuid4()))
    )
    result = worker.tick(clock.now + timedelta(minutes=30), MockPublisher(), MockHttpSender())
    assert result["delivered"] >= 1 and result["relayed"] >= 1
    later = worker.tick(clock.now + timedelta(days=1), MockPublisher(), MockHttpSender())
    assert set(later) == {"delivered", "held", "expired", "relayed", "webhooks", "errors"}
    assert later["errors"] == 0
