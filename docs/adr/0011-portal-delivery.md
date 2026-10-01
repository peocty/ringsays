# 0011 Portal build and delivery

Status: Accepted, 2026-10-01

## Decision
- The portal is a static React build served by unprivileged nginx. Configuration (`API_BASE`, `OIDC_AUTHORITY`, `OIDC_CLIENT_ID`, `OIDC_EXTRA_ORIGINS`, `PORTAL_TIME_ZONE`) is read when the container starts, served at `/config.js`, and validated by `nginx/15-ringsays.envsh`; the container refuses to start with a missing or malformed value. One image is promoted from staging to production.
- Strict Content Security Policy on every response: scripts, styles, fonts and images from the portal origin only, no inline code, no `data:` assets, network calls only to the API and identity provider origins. Browser tests run under the same policy and fail on any violation.
- Arabic is the default language. Layout uses logical CSS properties only, so right to left mirrors without separate styles. Dates use the Gregorian calendar and Latin digits in both languages and are shown and entered in `PORTAL_TIME_ZONE` (Asia/Riyadh for the KSA launch).
- API types are generated from `contracts/openapi/admin.yaml`; CI fails if the generated file is out of date.
- Source maps are produced for error tracking but never shipped in the image.

## Consequences
Translations need native speaker review before launch (as with purpose code texts). An India rollout sets `PORTAL_TIME_ZONE=Asia/Kolkata` and adds the needed languages.
