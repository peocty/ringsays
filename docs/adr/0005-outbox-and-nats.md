# 0005 Transactional outbox with NATS JetStream

Status: Accepted, 2026-10-01

## Decision
Domain events are written to an outbox table in same database transaction as state change, then relayed to NATS JetStream. Kafka adapter is available for tenants that mandate Kafka. Event payloads carry identifiers and status only, never phone numbers, names or free text.

## Consequences
No lost or phantom events from dual writes. NATS is light enough for on premise deployment at a bank.
