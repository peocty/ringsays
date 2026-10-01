"""HTTP endpoints of the MOCK OpenID Connect issuer (local environment only, see issuer.py)."""

from __future__ import annotations

import html
from typing import Annotated
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.core.config import DEV_OIDC_ISSUER, settings

from . import issuer

router = APIRouter(prefix="/dev/oidc", tags=["MOCK sign in"], include_in_schema=False)


def _csp() -> str:
    # form-action also governs where the form's redirect may go, so allow the portal origins.
    origins = " ".join(
        sorted({f"{u.scheme}://{u.netloc}" for u in map(urlsplit, settings.portal_redirect_uris)})
    )
    return (
        f"default-src 'none'; style-src 'unsafe-inline'; form-action 'self' {origins}; frame-ancestors 'none'"
    )


@router.get("/.well-known/openid-configuration")
def discovery() -> dict[str, object]:
    return {
        "issuer": DEV_OIDC_ISSUER,
        "authorization_endpoint": f"{DEV_OIDC_ISSUER}/authorize",
        "token_endpoint": f"{DEV_OIDC_ISSUER}/token",
        "jwks_uri": f"{DEV_OIDC_ISSUER}/jwks",
        "end_session_endpoint": f"{DEV_OIDC_ISSUER}/logout",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["openid", "email", "profile", "offline_access"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


@router.get("/jwks")
def jwks() -> dict[str, object]:
    return issuer.jwks()


def _error_page(message: str, status: int = 400) -> HTMLResponse:
    body = f"<!doctype html><title>MOCK sign in</title><p>{html.escape(message)}</p>"
    return HTMLResponse(body, status_code=status, headers={"Content-Security-Policy": _csp()})


@router.get("/authorize")
def authorize_page(
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    response_type: str = "code",
    code_challenge_method: str = "S256",
    nonce: str | None = None,
    scope: str = "openid",
) -> HTMLResponse:
    if client_id != issuer.CLIENT_ID or not issuer.redirect_allowed(redirect_uri):
        return _error_page("Unknown client or redirect address.")
    if response_type != "code" or code_challenge_method != "S256":
        return _error_page("Only authorization code with PKCE S256 is supported.")
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{html.escape(v, quote=True)}">'
        for k, v in {
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "nonce": nonce or "",
        }.items()
    )
    buttons = "".join(
        f'<button name="email" value="{html.escape(a.email, quote=True)}">'
        f"<b>{html.escape(a.name)}</b><br><small>{html.escape(a.email)} · {html.escape(a.note)}</small>"
        "</button>"
        for a in issuer.ACCOUNTS
    )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MOCK sign in</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:520px;margin:40px auto;padding:0 16px;color:#1d2433}}
.warn{{background:#fff4d6;border:1px solid #e9c46a;padding:10px 12px;border-radius:8px;font-size:14px}}
button{{display:block;width:100%;text-align:start;margin:8px 0;padding:10px 12px;border:1px solid #ccd3e0;
border-radius:8px;background:#fff;cursor:pointer;font:inherit}}
button:hover{{border-color:#2f5bea}} input[type=email]{{width:100%;padding:10px;box-sizing:border-box;
border:1px solid #ccd3e0;border-radius:8px;font:inherit}}
</style></head><body>
<h1>RingSays MOCK sign in</h1>
<p class="warn">Local development only. A real deployment uses your organisation's identity provider.</p>
<form method="post" action="{DEV_OIDC_ISSUER}/authorize">{hidden}
{buttons}
<p>Or another email (for example one you invited):</p>
<input type="email" name="other_email" placeholder="name@example.com">
<button name="email" value="">Sign in with this email</button>
</form></body></html>"""
    return HTMLResponse(page, headers={"Content-Security-Policy": _csp(), "Cache-Control": "no-store"})


@router.post("/authorize", response_model=None)
def authorize_submit(
    redirect_uri: Annotated[str, Form()],
    state: Annotated[str, Form()],
    code_challenge: Annotated[str, Form()],
    email: Annotated[str, Form()] = "",
    other_email: Annotated[str, Form()] = "",
    nonce: Annotated[str, Form()] = "",
) -> RedirectResponse | HTMLResponse:
    if not issuer.redirect_allowed(redirect_uri):
        return _error_page("Unknown redirect address.")
    chosen = (email or other_email).strip().lower()
    if "@" not in chosen or len(chosen) > 254:
        return _error_page("Enter an email address.")
    name = next((a.name for a in issuer.ACCOUNTS if a.email == chosen), chosen)
    code = issuer.issue_code(chosen, name, redirect_uri, code_challenge, nonce or None)
    return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", status_code=303)


@router.post("/token")
def token(
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    code: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
    refresh_token: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    try:
        if grant_type == "authorization_code" and code and redirect_uri and code_verifier:
            body = issuer.exchange(code, redirect_uri, client_id, code_verifier)
        elif grant_type == "refresh_token" and refresh_token:
            body = issuer.refresh(refresh_token, client_id)
        else:
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    except issuer.TokenError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


@router.get("/logout", response_model=None)
def logout(
    post_logout_redirect_uri: Annotated[str | None, Query()] = None,
) -> RedirectResponse | HTMLResponse:
    if post_logout_redirect_uri:
        target = urlsplit(post_logout_redirect_uri)
        allowed = {f"{u.scheme}://{u.netloc}" for u in map(urlsplit, settings.portal_redirect_uris)}
        if f"{target.scheme}://{target.netloc}" in allowed:
            return RedirectResponse(post_logout_redirect_uri, status_code=303)
    return _error_page("Signed out.", status=200)
