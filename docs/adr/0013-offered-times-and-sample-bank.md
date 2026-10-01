# 0013 Organisation offered times, local webhooks and the Mock Bank sample

Status: Accepted, 2026-10-02

## Decision
- **Offered times.** An organisation may offer up to five call times when it creates an intent
  (`offered_slots`); the customer may pick one (SCHEDULE). A customer may also suggest times
  (PROPOSE); the organisation confirms one with `POST /v1/intents/{id}/schedule`. Every slot, from
  either side, lasts at most 120 minutes, ends at most 7 days ahead and is distinct, so a slot can
  never be used to keep an intent, or the consent behind it, open. Agreed times extend the intent's
  validity, and its Context Token lasts as long.
- **Suggested times** on the phone start between 09:00 and 20:00 on the customer's own clock, at
  least an hour ahead, and never after the intent's `deadline` (now part of the intent display).
- **Local webhooks.** In the local environment only, webhook endpoints may be `http://127.0.0.1` or
  `http://localhost` with a path, and the worker really sends to them, so a bank can develop its
  receiver on one machine. Everywhere else validation, the database check's https branch and the
  production sender (public addresses only, port 443) are unchanged. Outside local, every portal
  origin must be https or the API refuses to start.
- **Mock Bank** (`examples/mock-bank`) is the reference integration handed to bank teams: OAuth with
  cached token, an idempotency key per bank operation, signed webhook verification with replay window,
  dedupe and ordering, Context Token kept server side and passed only to the owning customer's app,
  and the SDK card in the bank's own app. Demo shortcuts are marked in code and guide.

## Consequences
Banks get a working pattern to copy and a test bed for their own receivers. The SDK sample needs
device testing like the RingSays app. A re-issue endpoint for Context Tokens lost in transit remains open.
