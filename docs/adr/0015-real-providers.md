# 0015 Real SMS and push providers

Status: accepted (2026-10-02)

## Context

Production refuses MOCK adapters, so nobody can sign in (SMS code) and no intent reaches a phone
(push) until real providers exist. The launch market is KSA: SMS sender names must be registered with
CST and are sent through licensed local aggregators. Push goes through Google (FCM) for Android and
Apple (APNs) for iOS; no in Kingdom alternative reaches stock phones.

## Decision

- **Two SMS adapters, one setting.** Taqnyat (`POST /v1/messages`, bearer token, JSON, 201) and
  Unifonic (`POST /rest/SMS/messages`, AppSid form, `success` true). `RINGSAYS_SMS_PROVIDER` picks one;
  `RINGSAYS_SMS_API_KEY` holds the token or AppSid. Both are Saudi providers, so a code and its number
  stay with a KSA licensed company. Switching provider is one setting and one secret, no code change.
- **Failure is explicit.** Any provider error (status, refused recipient, timeout of 5 s) raises
  `SmsSendFailed`; the API answers `503 sms_unavailable` with no provider detail, and the code request
  still counts against the limits (no free retries through errors).
- **Push by platform.** FCM HTTP v1 with the workload's own Google identity (no key file); APNs over
  HTTP/2 with a token signing key (.p8), JWT reused 40 minutes, one long lived connection per process
  as Apple asks. A device token that is not hex is refused before it reaches the URL.
- **Minimal payload.** A push carries the intent id, its kind and a fixed bilingual text; the app
  fetches the details over its own authenticated session. Nothing about the customer, the caller or
  the purpose passes through Google or Apple.
- **Fail at start, not per message.** Outside local, settings refuse real mode with any provider value
  missing; an unreadable Apple key stops the process at start.
- **Logs** carry the provider name and the provider's message id only, never a number or a code.

## Consequences

- Production needs: a CST registered sender name, a Taqnyat or Unifonic account, an Apple developer
  key and a Firebase project (deploy/README.md, "Real providers").
- Push failures follow the delivery ladder already in place (next channel on the next tick).
- Adapters are tested against each provider's documented behaviour with recorded responses; a check
  against live accounts is part of the first production verification.
- Staging keeps MOCK so test codes never reach real phones.
