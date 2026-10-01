# 0004 Intent lifecycle state machine

Status: Accepted, 2026-10-01

## Decision
`contracts/state-machines/intent.yaml` is canonical. Backend `state_machine.py` applies it; every change returns a new immutable Intent plus an IntentEvent. Server is authoritative; clients only use the table to choose which buttons to show.

Refinements over foundation doc section H, found while writing tests:

| Change | Reason |
| --- | --- |
| CALLER may cancel from DELIVERED and ACCEPTED | Enterprise "withdraw before contact" must work after delivery, not only before |
| RECEIVER may decline from SCHEDULED | Customer changes mind after agreeing a slot |
| RESCHEDULED can EXPIRE | A proposal nobody answers must not stay open forever |
| RESCHEDULED to DECLINED is RECEIVER only | Caller rejecting a proposal is a cancel, not a decline |
| MESSAGE response maps to DECLINED with reason MESSAGE_INSTEAD | Keeps terminal set small; webhook still tells tenant customer asked for a message |

Timing rules: scheduling extends `valid_until` to slot end plus 15 minutes; a scheduled call may start at most 10 minutes early; LATER accepts 15, 30, 60 or 120 minutes; at most 5 proposed slots, all in future and inside deadline.

## Consequences
Foundation doc section H transition table needs updating to match this ADR.
