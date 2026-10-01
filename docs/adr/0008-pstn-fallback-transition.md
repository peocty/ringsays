# 0008 PSTN fallback: DELIVERED may move to IN_PROGRESS

Status: Accepted, 2026-10-01

## Context
Fallback ladder (foundation doc section 13) ends at a plain telephone call with no RingSays enrichment. When no digital channel reaches the customer (no RingSays app, no tenant SDK session, receiver rules block app delivery), nobody can accept or schedule, yet the bank still needs to call.

## Decision
Delivery records `channel_used = PSTN` and marks the intent DELIVERED, meaning "ready for a plain call". Webhook `intent.delivered` tells the tenant which channel was used, so the agent knows to dial without waiting. New transition DELIVERED to IN_PROGRESS is allowed for SYSTEM only, and `start_call` enforces that it applies only when `channel_used` is PSTN. Digitally delivered intents still require receiver agreement.

## Consequences
Contact rate analytics can separate negotiated calls from fallback calls. Receiver rules that block app delivery do not prevent a bank's ordinary phone call, which RingSays cannot and should not stop.
