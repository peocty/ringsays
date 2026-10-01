# Mock Bank: RingSays integration sample

Fictional bank, built as a bank's own team would integrate RingSays. Copy the patterns, not the
demo shortcuts (marked **DEMO ONLY** below).

```text
 Agent console ──► Mock Bank server ──► RingSays enterprise API   (OAuth, intents, schedule, outcome)
                        ▲     │
       signed webhooks ─┘     └──► Mock Bank app (bank's own sign in) ──► RingSays SDK (Context Token)
```

## Run (local MOCK environment)

```bash
make api                     # RingSays API, RINGSAYS_OTP_PER_IP_PER_HOUR not needed here
cd backend && .venv/bin/python -m app.worker   # delivery and webhooks (second terminal)
make bank-setup              # fresh MOCK tenant + webhook to 127.0.0.1:4100; prints console password
make bank                    # Mock Bank server: http://127.0.0.1:4100/console
make bank-app                # customer app web preview: http://127.0.0.1:8082 (or Expo Go on a phone)
```

Customers: Noura (Arabic), Faisal (Arabic), Priya (English); demo PIN `2468`.
Browser tests of the whole flow: `make e2e-bank`.

## Flow

1. Agent picks customer, approved purpose, priority, minutes, optional last four of a reference and
   up to three offered times. Server calls `POST /v1/intents` with `channel_preference: [SDK, PRECALL_PUSH, PSTN]`.
2. Response carries `context_token`. Server keeps it against the customer; console never sees it.
3. Customer opens the bank app (bank's own sign in). `/app/messages` hands the token to that
   customer only, only while it can still be answered. App renders `<IntentCard token=… />`.
4. Customer answers on the card (talk now, later, pick an offered time, suggest times, decline).
5. RingSays sends signed webhooks; console updates. Agent calls (`/calling`), confirms a suggested
   time (`/schedule`), records an outcome (`/outcome`) or withdraws (`/cancel`).

## Patterns to copy

| Concern | How here | File |
| --- | --- | --- |
| Access token | client credentials, cached, refreshed 60 s early, single flight, one retry on 401 | `server/src/ringsays.ts` |
| Safe retries | one Idempotency-Key per bank operation (console request id), reused on retry; 10 s limit per call | `ringsays.ts`, `app.ts` |
| Webhook trust | HMAC SHA-256 over `t.` + raw body, ±300 s window, constant time compare, several `v1` accepted | `server/src/webhook.ts` |
| Webhook order | dedupe by `event_id`, ignore events older than the last applied, 503 for intents not stored yet (RingSays retries) | `server/src/store.ts`, `app.ts` |
| Webhook vs API | webhook is the signal; on scheduled or rescheduled the server rereads `GET /v1/intents/{id}` | `app.ts` |
| Context Token | stored server side, given only to the owning customer's app session, never logged or shown to staff | `app.ts` |
| Customer app | bank's own authentication; SDK gets brand colours only, trust badge stays RingSays' | `app/src/App.tsx` |
| Console | password sign in, SameSite Strict HttpOnly cookie (Secure over https), CSRF header, strict CSP, DOM text only | `server/console/` |

## DEMO ONLY (do not copy)

- `/app/customers` lists customers for a picker; PIN sign in stands in for the bank's real login.
- Console password from `.env.local`; a bank uses its staff identity provider and roles.
- State in a JSON file; a bank uses its database, Context Tokens encrypted or kept only until the intent ends.
- App polls every 5 s; a bank also uses its own push to tell the app something new arrived.
- Loopback webhooks over http work only in RingSays local environment; everywhere else webhook URLs
  must be https on port 443 to a public address.

## Limits

- If the first create response is lost, the retried call returns the same intent without a token
  (RingSays shows tokens once); the request then reaches the customer by the next channel.
- SDK card declines with reason "not now"; other reasons through `useIntent`.
