# 0002. Modular monolith: API, workers, scheduler

- Status: Accepted
- Date: 2026-11-02

## Context
NeedWise has many capabilities (identity, knowledge, retrieval, agents, workflows, meetings, BI)
and is built by a very small team. Microservices would multiply deployment, observability and
consistency work long before any service needs independent scaling.

## Decision
One Python codebase (`apps/api`) with strict module boundaries under `needwise/modules/`.
Modules interact only through their public service interfaces, enforced by import-linter
(from Phase 1). The same image runs as three process types: `api` (HTTP/SSE), `worker`
(Celery) and `scheduler` (Celery beat).

## Consequences
- One deploy, one migration history, in-process calls, simple local development.
- Heavy work (parsing, embedding) is isolated by queue and process type, not by service.
- A module can be extracted into a service later if measurements justify it; the enforced
  boundaries keep that possible.
