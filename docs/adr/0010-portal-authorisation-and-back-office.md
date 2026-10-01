# 0010 Portal sign in, authorisation and the back office

Status: Accepted, 2026-10-01

## Decision
- People sign in to the portal with OpenID Connect (authorization code with PKCE). The identity provider proves who they are (issuer, subject, verified email); RingSays decides what they may do. Memberships and roles are stored by RingSays and read on every request, so disabling a person or removing a role takes effect at once, not at token expiry.
- Tenant identity provider and RingSays staff identity provider are configured separately (`RINGSAYS_ADMIN_OIDC_ISSUER`, `RINGSAYS_STAFF_OIDC_ISSUER`). Only the tenant provider can accept tenant invitations; only the staff provider can sign in to the back office. Locally both are the MOCK issuer.
- An invitation (email plus roles) becomes a membership on first sign in with that verified email, within 14 days. Inviting again before acceptance renews it. Providers that do not send `email_verified` but only issue tokens for accounts they own (for example a bank's own Microsoft Entra ID tenant) are listed in `RINGSAYS_OIDC_EMAIL_TRUSTED_ISSUERS`.
- Roles map to permissions in one table (`app/modules/admin/access.py`). Separation of duties: only integration admins create credentials and webhooks; nobody changes their own roles; the last active administrator cannot be removed. The portal hides what a role cannot do; the API enforces it.
- The back office runs as a separate internal deployment with its own database role `ringsays_backoffice`. That role may read organisation, catalogue, verification and audit rows of every tenant, but has no access to intents or identity. The tenant facing deployment sets `RINGSAYS_BACKOFFICE_ENABLED=false`, holds no back office credential and answers 404 on back office paths.
- Approval belongs to the back office only. Column grants limit what the API role can write; guard triggers (allow list: owner and back office) refuse any other role that tries to approve a purpose code, verify a number, change a verified profile, or change evidence under review. Guards raise SQLSTATE `RSG01`, returned as 409.
- Every write and every RingSays decision, including staff downloads of evidence, is appended to the affected tenant's own audit chain. The tenant's compliance team can export it as CSV and recompute every hash.
- Suspension stops API access within 30 seconds and refuses changes, except containment: revoking credentials, disabling webhook endpoints, stopping calling numbers and disabling people.

## Consequences
- Production needs two identity provider configurations and two deployments of the same image (tenant facing, back office).
- The portal's browser session uses short access tokens renewed with a refresh token (`offline_access`). A rejected session never redirects on its own; the person chooses to sign in again, so typed work is not lost and redirect loops cannot happen.
- Customer numbers are searched by POST body and matched by keyed hash, so they never appear in URLs or access logs.
