# 0006 No server side contact discovery in MVP 1

Status: Accepted, 2026-10-01

## Context
Hashing phone numbers does not protect them: number space is small enough to reverse every hash.

## Decision
MVP 1 has no discovery endpoint. Enterprise intents are addressed by phone number tenant already holds. Display names for incoming consumer intents are matched to local contacts on device. MVP 2 adds discovery using an oblivious pseudorandom function with per account quotas.

## Consequences
"Phonebook upload: never" is true by construction in MVP 1, which simplifies PDPL assessment.
