"""Organisation offered times (SCHEDULE by the customer) and the organisation accepting a proposed time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.scripts.seed import SeededTenant

from .conftest import Clock, auth, intent_body, token_for

pytestmark = pytest.mark.integration
OPENS = timedelta(minutes=30)
S1 = {"start": "2026-10-04T10:00:00Z", "end": "2026-10-04T10:10:00Z"}
S2 = {"start": "2026-10-04T12:00:00+03:00", "end": "2026-10-04T12:10:00+03:00"}


def _create(client: TestClient, t: SeededTenant, **kw: object) -> dict:
    r = client.post(
        "/v1/intents", json=intent_body(t, **kw), headers=auth(token_for(client, t), str(uuid4()))
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_offered_slots_customer_schedules_through_sdk(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    created = _create(client, tenant, offered_slots=[S1, S2])
    bank = auth(token_for(client, tenant))
    detail = client.get(f"/v1/intents/{created['intent_id']}", headers=bank).json()
    assert len(detail["proposed_slots"]) == 2
    clock.now += OPENS
    dev = {"RingSays-Device-Id": str(uuid4()), "Accept-Language": "en"}
    shown = client.get(f"/v1/tokens/{created['context_token']}", headers=dev).json()
    assert "SCHEDULE" in shown["actions"] and len(shown["proposed_slots"]) == 2
    r = client.post(
        f"/v1/tokens/{created['context_token']}/respond", headers=dev, json={"action": "SCHEDULE", "slot": S2}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SCHEDULED"
    after = client.get(f"/v1/intents/{created['intent_id']}", headers=bank).json()
    assert datetime.fromisoformat(after["scheduled_slot"]["start"]) == datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
    assert after["proposed_slots"] == []


def test_offered_slot_rules(client: TestClient, tenant: SeededTenant) -> None:
    h = auth(token_for(client, tenant), str(uuid4()))
    past = {"start": "2026-10-01T10:00:00Z", "end": "2026-10-01T10:10:00Z"}
    assert (
        client.post("/v1/intents", json=intent_body(tenant, offered_slots=[past]), headers=h).status_code
        == 422
    )
    backwards = {"start": "2026-10-04T10:10:00Z", "end": "2026-10-04T10:00:00Z"}
    h2 = auth(token_for(client, tenant), str(uuid4()))
    assert (
        client.post(
            "/v1/intents", json=intent_body(tenant, offered_slots=[backwards]), headers=h2
        ).status_code
        == 400
    )
    h3 = auth(token_for(client, tenant), str(uuid4()))
    six = [{"start": f"2026-10-04T1{i}:00:00Z", "end": f"2026-10-04T1{i}:10:00Z"} for i in range(6)]
    assert (
        client.post("/v1/intents", json=intent_body(tenant, offered_slots=six), headers=h3).status_code == 400
    )


def test_organisation_accepts_customer_proposal(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    created = _create(client, tenant)
    clock.now += OPENS
    dev = {"RingSays-Device-Id": str(uuid4())}
    token = created["context_token"]
    assert client.get(f"/v1/tokens/{token}", headers=dev).status_code == 200
    r = client.post(
        f"/v1/tokens/{token}/respond", headers=dev, json={"action": "PROPOSE", "proposed_slots": [S1, S2]}
    )
    assert r.json()["status"] == "RESCHEDULED"
    iid = created["intent_id"]
    bank = token_for(client, tenant)
    proposed = client.get(f"/v1/intents/{iid}", headers=auth(bank)).json()["proposed_slots"]
    assert len(proposed) == 2

    other = {"start": "2026-10-04T15:00:00Z", "end": "2026-10-04T15:10:00Z"}
    bad = client.post(f"/v1/intents/{iid}/schedule", json={"slot": other}, headers=auth(bank, str(uuid4())))
    assert bad.status_code == 422
    key = str(uuid4())
    ok = client.post(f"/v1/intents/{iid}/schedule", json={"slot": S1}, headers=auth(bank, key))
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "SCHEDULED" and ok.json()["scheduled_slot"]["start"].startswith(
        "2026-10-04T10:00"
    )
    again = client.post(f"/v1/intents/{iid}/schedule", json={"slot": S1}, headers=auth(bank, key))
    assert again.status_code == 200, "idempotent replay"
    shown = client.get(f"/v1/tokens/{token}", headers=dev).json()
    assert shown["status"] == "SCHEDULED"
    late = client.post(f"/v1/intents/{iid}/schedule", json={"slot": S2}, headers=auth(bank, str(uuid4())))
    assert late.status_code in (409, 422)


def test_slot_bounds_offered_and_proposed(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    far = {"start": "2026-10-20T10:00:00Z", "end": "2026-10-20T10:10:00Z"}  # beyond 7 days
    long = {"start": "2026-10-04T10:00:00Z", "end": "2026-10-04T13:00:00Z"}  # 3 hours
    early = {"start": "2026-10-04T07:10:00Z", "end": "2026-10-04T07:20:00Z"}  # before valid_from
    for bad in ([far], [long], [early], [S1, S1]):
        h = auth(token_for(client, tenant), str(uuid4()))
        r = client.post("/v1/intents", json=intent_body(tenant, offered_slots=bad), headers=h)
        assert r.status_code == 422, (bad, r.text)

    created = _create(client, tenant)
    clock.now += OPENS
    dev = {"RingSays-Device-Id": str(uuid4())}
    token = created["context_token"]
    client.get(f"/v1/tokens/{token}", headers=dev)
    for bad in ([far], [long]):
        r = client.post(
            f"/v1/tokens/{token}/respond", headers=dev, json={"action": "PROPOSE", "proposed_slots": bad}
        )
        assert r.status_code == 422, r.text


def test_token_lives_as_long_as_the_agreed_time(
    client: TestClient, tenant: SeededTenant, clock: Clock
) -> None:
    # Window closes 08:00; customer picks the bank's 12:00 time; token must still work at 11:00.
    created = _create(client, tenant, offered_slots=[S2])
    clock.now += OPENS
    dev = {"RingSays-Device-Id": str(uuid4())}
    token = created["context_token"]
    client.get(f"/v1/tokens/{token}", headers=dev)
    r = client.post(f"/v1/tokens/{token}/respond", headers=dev, json={"action": "SCHEDULE", "slot": S2})
    assert r.json()["status"] == "SCHEDULED"
    clock.now = datetime(2026, 10, 4, 8, 0, tzinfo=UTC)  # 11:00 Riyadh, after the original window
    shown = client.get(f"/v1/tokens/{token}", headers=dev)
    assert shown.status_code == 200 and shown.json()["status"] == "SCHEDULED"
    assert "DECLINE" in shown.json()["actions"]


def test_accept_clears_offered_times(client: TestClient, tenant: SeededTenant, clock: Clock) -> None:
    created = _create(client, tenant, offered_slots=[S1])
    clock.now += OPENS
    dev = {"RingSays-Device-Id": str(uuid4())}
    client.get(f"/v1/tokens/{created['context_token']}", headers=dev)
    client.post(f"/v1/tokens/{created['context_token']}/respond", headers=dev, json={"action": "ACCEPT"})
    detail = client.get(f"/v1/intents/{created['intent_id']}", headers=auth(token_for(client, tenant))).json()
    assert detail["status"] == "ACCEPTED" and detail["proposed_slots"] == []
