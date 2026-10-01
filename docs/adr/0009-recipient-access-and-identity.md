# 0009 RingSays accounts, recipient access and number recycling

Status: Accepted, 2026-10-01

## Decision
- Users are found by a keyed hash of their phone number (HMAC with a pepper held outside the database). The number itself is stored only encrypted, for the user's own data export.
- User data (devices, refresh tokens, preferences, consents) is isolated by forced row level security on `app.user_id`.
- A user reads intents addressed to them through a second, read only policy on `intent.intents`: `to_phone_hash` must match and the intent must be created on or after the account was created. Intents to the same number before that belong to a previous holder (erased account or recycled number) and are never shown.
- Receivers change intents only through the server: ownership is checked in the user scope, then the change runs in the tenant scope, so tenant events, webhooks and audit are recorded exactly as for any other change.
- Sign in: 6 digit code, 5 minutes, 5 tries, earlier codes invalidated by a new one; per phone, per network and platform wide request limits; daily cap on wrong codes per phone across codes. All limits fail closed.
- Sessions: 15 minute access token bound to device; 30 day refresh token rotated on use, signed by the device's P-256 key; reuse of a rotated token revokes the whole token family. Sign out and erasure are published to a shared revocation list so every API process honours them at once.
- Consent ledger: the first delivery that reaches a user's device from an organisation creates an entry. Withdrawal stops RingSays push and removes that organisation from the RingSays inbox, URGENT included. The organisation's own app (SDK) and ordinary phone calls remain the organisation's channels.

## Consequences
A customer who installs RingSays after a bank has already sent an intent will not see that earlier intent in the RingSays app (they still get the bank's call or the bank app's display). This is deliberate: on a recycled number, that intent was meant for someone else.
