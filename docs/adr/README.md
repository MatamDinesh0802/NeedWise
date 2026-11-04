# Architecture Decision Records

Short records of significant decisions: the context, the choice, and its consequences.
A decision is never edited after acceptance; a new ADR supersedes it.

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-modular-monolith.md) | Modular monolith: API, workers, scheduler | Accepted |
| [0003](0003-bff-authentication-with-keycloak.md) | Backend-for-Frontend authentication with Keycloak | Accepted |
| [0004](0004-apache-2-open-core-and-dco.md) | Apache-2.0 open core with `ee/`, contributions under DCO | Accepted |
| [0005](0005-postgres-roles-and-rls.md) | Separate owner/app database roles; Row-Level Security fails closed | Accepted |
| [0006](0006-two-lock-permission-aware-retrieval.md) | Two-lock permission-aware retrieval | Accepted (implemented) |
| [0007](0007-model-providers-and-lightweight-parsing.md) | Claude by default, local embeddings, swappable adapters; lightweight parsing | Accepted |
| [0008](0008-retrieval-fusion-and-sensitivity-routing.md) | Tuned rank fusion, collection-level recheck, routing by sensitivity | Accepted |
