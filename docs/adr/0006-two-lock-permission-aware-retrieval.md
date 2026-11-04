# 0006. Two-lock permission-aware retrieval

- Status: Accepted (implementation: Phase 3)
- Date: 2026-11-02

## Context
Retrieval must never return content a user cannot access, including through agents.
Filtering after vector search hurts recall (top-k fills with forbidden chunks) and is easy to
forget. Checking each chunk against the authorization service at query time is too slow.

## Decision
1. **Lock 1, pre-filter:** each chunk in Qdrant carries an `acl` payload of principals
   (`user:…`, `group:…`, `dept:…`, `org:…`). Every search filters on the user's principals,
   tenant and active status, inside the vector-store repository so callers cannot skip it.
2. **Lock 2, recheck:** documents behind the final chunks are checked with an OpenFGA
   `BatchCheck` before generation. Mismatches are dropped and counted
   (`authz_projection_drift_total`), which alerts.
- ACL payloads store usersets, not expanded user lists, so membership changes need no re-index.
- The system never reveals that an inaccessible document exists.

## Consequences
- Fast, exact filtering with no recall penalty, plus defense in depth.
- Sharing changes require a background payload update.
- A red-team suite with canary tokens (Phase 3) must show zero leaks for any change to merge.
