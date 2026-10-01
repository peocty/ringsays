"""Rate limits and contact policy through the live API, backed by a real Redis test database."""

from __future__ import annotations

from uuid import uuid4

import pytest
import redis
from fastapi.testclient import TestClient

from app.core.config import settings
from app.platform import ratelimit
from app.scripts.seed import SeededTenant

from .conftest import auth, intent_body, token_for

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _need_redis() -> None:
    try:
        redis.Redis.from_url("redis://127.0.0.1:6379/15", socket_timeout=0.5).ping()
    except redis.RedisError:
        pytest.skip("Redis not reachable")


def _post(client: TestClient, tok: str, t: SeededTenant, key: str | None = None, **overrides: object):  # type: ignore[no-untyped-def]
    return client.post(
        "/v1/intents", json=intent_body(t, **overrides), headers=auth(tok, key or str(uuid4()))
    )


def test_recipient_daily_contact_limit(client: TestClient, tenant: SeededTenant) -> None:
    settings.recipient_intents_per_tenant_per_day = 3
    tok = token_for(client, tenant)
    assert [_post(client, tok, tenant).status_code for _ in range(3)] == [201, 201, 201]
    r = _post(client, tok, tenant)
    assert r.status_code == 429
    assert r.json()["code"] == "rate_limited"
    assert int(r.headers["Retry-After"]) > 0
    assert _post(client, tok, tenant, to={"phone": "+966500000077"}).status_code == 201, (
        "other recipient fine"
    )


def test_limit_is_per_tenant(client: TestClient, tenant: SeededTenant, other_tenant: SeededTenant) -> None:
    settings.recipient_intents_per_tenant_per_day = 1
    assert _post(client, token_for(client, tenant), tenant).status_code == 201
    assert _post(client, token_for(client, other_tenant), other_tenant).status_code == 201


def test_idempotent_retry_does_not_consume_quota(client: TestClient, tenant: SeededTenant) -> None:
    settings.recipient_intents_per_tenant_per_day = 1
    tok = token_for(client, tenant)
    key = str(uuid4())
    assert _post(client, tok, tenant, key).status_code == 201
    assert _post(client, tok, tenant, key).status_code == 200, "replay returns original, not 429"


def test_urgent_daily_quota(client: TestClient, tenant: SeededTenant) -> None:
    settings.tenant_urgent_per_day = 2
    tok = token_for(client, tenant)
    urgent = {"purpose_code": "CARD.TRANSACTION.VERIFY", "priority": "URGENT", "expected_duration_min": 3}
    codes = [
        _post(client, tok, tenant, to={"phone": f"+96650000010{i}"}, **urgent).status_code for i in range(3)
    ]
    assert codes == [201, 201, 429]
    assert _post(client, tok, tenant, to={"phone": "+966500000109"}).status_code == 201, "NORMAL unaffected"


def test_refused_request_consumes_nothing(client: TestClient, tenant: SeededTenant) -> None:
    settings.tenant_urgent_per_day = 0
    settings.recipient_intents_per_tenant_per_day = 1
    tok = token_for(client, tenant)
    urgent = {"purpose_code": "CARD.TRANSACTION.VERIFY", "priority": "URGENT", "expected_duration_min": 3}
    assert _post(client, tok, tenant, **urgent).status_code == 429
    assert _post(client, tok, tenant).status_code == 201, "recipient counter untouched by refused request"


def test_redis_outage_fails_open(client: TestClient, tenant: SeededTenant) -> None:
    ratelimit.set_limiter(
        ratelimit.Limiter(redis.Redis.from_url("redis://127.0.0.1:1/0", socket_timeout=0.2))
    )
    assert _post(client, token_for(client, tenant), tenant).status_code == 201


def test_redis_holds_no_phone_numbers(client: TestClient, tenant: SeededTenant) -> None:
    _post(client, token_for(client, tenant), tenant)
    keys = [k.decode() for k in redis.Redis.from_url("redis://127.0.0.1:6379/15").keys("*")]
    assert keys and not any("966500000001" in k or "500000001" in k for k in keys)
