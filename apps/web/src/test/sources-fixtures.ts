/** Sources as GET /api/v1/admin/sources returns them (apps/api/src/workmind/api/v1/sources.py). */

import type { Source, SourceRun, StaleSyncedDocument } from "@/lib/admin/sources";

export function runFixture(overrides: Partial<SourceRun> = {}): SourceRun {
  return {
    id: "run-1",
    trigger: "schedule",
    requested_by: null,
    status: "ok",
    started_at: "2026-11-13T08:00:00Z",
    finished_at: "2026-11-13T08:00:12Z",
    complete: true,
    stats: { items: 14, added: 2, updated: 1, unchanged: 11, deleted: 0, failed: 0, missing: 0 },
    errors: [],
    ...overrides,
  };
}

export function sourceFixture(overrides: Partial<Source> = {}): Source {
  return {
    id: "s1",
    key: "handbook-site",
    name: "Handbook site",
    kind: "web",
    collection: "company-handbook",
    collection_id: "c-handbook",
    collection_name: "Company Handbook",
    collection_scope: "enterprise",
    access: [
      { principal: "user:u-alice", relation: "owner", label: "Alice Moreau" },
      { principal: "dept:d-hr", relation: "editor", label: "People & HR (department)" },
      { principal: "org:t-acme", relation: "viewer", label: "Everyone in the organization" },
    ],
    owner: { id: "u-erin", display_name: "Erin Takahashi" },
    config: { start_urls: ["https://intranet.example/handbook/"], max_pages: 100 },
    has_credentials: false,
    needs: [],
    oauth: null,
    interval_minutes: 60,
    enabled: true,
    status: "ok",
    health: "healthy",
    syncing: false,
    consecutive_failures: 0,
    failure_alerted_at: null,
    last_sync_at: "2026-11-13T08:00:00Z",
    last_success_at: "2026-11-13T08:00:12Z",
    next_sync_at: "2026-11-13T09:00:00Z",
    last_error: null,
    last_error_plain: null,
    last_run: runFixture(),
    stats: { items: 14, added: 2, updated: 1, unchanged: 11, deleted: 0, failed: 0 },
    documents: 14,
    stale_after_days: 180,
    stale: { not_updated: 0, missing_upstream: 0 },
    ...overrides,
  };
}

export function staleFixture(overrides: Partial<StaleSyncedDocument> = {}): StaleSyncedDocument {
  return {
    id: "d-travel",
    title: "Travel policy",
    url: "https://intranet.example/handbook/travel.html",
    collection_id: "c-handbook",
    collection_name: "Company Handbook",
    source: "handbook-site",
    source_name: "Handbook site",
    reason: "not_updated",
    changed_at: "2025-09-01T10:00:00Z",
    missing_since: null,
    owner: { id: "u-erin", display_name: "Erin Takahashi" },
    reminded_at: null,
    ...overrides,
  };
}
