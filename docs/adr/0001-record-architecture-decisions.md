# 0001. Record architecture decisions

- Status: Accepted
- Date: 2026-11-02

## Context
WorkMind is built in public and will have outside contributors. Design decisions need to be
discoverable and their reasons preserved, so they are not re-litigated or silently reversed.

## Decision
Every significant decision gets a short ADR in `docs/adr/`, numbered sequentially, with
Context, Decision and Consequences sections. Accepted ADRs are immutable; a new ADR supersedes
an old one.

## Consequences
Reviewers can point to an ADR instead of re-explaining. Changing a recorded decision requires
a written argument.
