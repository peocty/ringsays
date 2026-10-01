"""Local development helpers. Mounted only in local environment with MOCK adapters (see main.py).

The MOCK SMS sender keeps messages in memory instead of sending them; this lets a developer, or the
mobile app's browser tests, read the code that would have arrived by SMS.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.modules.identity import service as identity

router = APIRouter(prefix="/dev", tags=["MOCK tools"], include_in_schema=False)


@router.get("/sms/last-code")
def last_code(phone: Annotated[str, Query(pattern=r"^\+[1-9]\d{6,14}$")]) -> dict[str, str]:
    sms = identity.get_sms()
    if not isinstance(sms, identity.MockSmsSender):
        raise HTTPException(status_code=404, detail="not_found")
    try:
        code = sms.last_code_for(phone)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="not_found") from exc
    return {"phone": phone, "code": code, "note": "MOCK SMS, local development only"}
