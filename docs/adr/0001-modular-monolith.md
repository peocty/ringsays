# 0001 Modular monolith with strict module boundaries

Status: Accepted, 2026-10-01

## Context
Specification lists 23 services. A team of three to seven engineers cannot operate 23 deployables before first pilot, and bank risk teams review every deployable.

## Decision
One FastAPI deployable (`backend/app`) with twelve modules: identity, enterprise, intent, context, preference, scheduling, trust, delivery, realtime, outcome, privacy, audit. Each module owns its own PostgreSQL schema. Modules call each other only through `service.py` interfaces; CI enforces this with an import linter. Realtime signalling runs as a separate process because long lived WebSocket connections scale differently.

## Consequences
Single deployment and simple local setup. A module can be extracted into its own service later without changing callers, because callers already depend on its interface. Extraction triggers are listed in foundation doc section G.
