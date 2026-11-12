# Web app

Next.js (App Router) frontend. Pre-alpha: the app shell, navigation and a live
system-status card are in place; each section shows the roadmap phase in which it
arrives (see `docs/ROADMAP.md` at the repo root).

## Develop

```bash
pnpm install
pnpm dev            # http://localhost:3400, proxies /api/* to API_INTERNAL_URL (default http://localhost:8400)
```

In Docker, Caddy serves this app and the API on one origin, so the `/api` proxy is only
active in development.

## Checks (run in CI)

```bash
pnpm lint
pnpm typecheck      # next typegen && tsc --noEmit
pnpm test           # Vitest
pnpm build          # standalone output for the Docker image
```

## Where things live

| Path | Purpose |
|---|---|
| `src/config/site.ts` | Product name and navigation — the only place the product name is defined |
| `src/components/page-placeholder.tsx` | Placeholder for sections not built yet (title, description, phase from the nav config) |
| `src/components/system-status-card.tsx` | Live health of the core services (`GET /api/v1/system/status`) |
| `src/lib/status-poller.ts`, `src/lib/system-status.ts` | Framework-free polling and view logic (unit-tested) |
| `src/components/ui/` | shadcn/ui components (Base UI primitives) |
| `src/lib/analytics/`, `src/components/insights/` | Insights (Phase 10): the analytics API client, formatting, chart shaping, and the page |
| `src/components/admin/analytics-section.tsx` | Admin › Analytics: dataset tables (upload, delete) and the semantic model YAML |

## Insights (Phase 10)

`/insights` asks `GET /api/v1/analytics/model` first and says plainly when the caller has no
access or nothing is set up. An answer (`POST /api/v1/analytics/ask`, 10–30 s, one request, no
streaming) shows a chart, the narrative, and every query with its SQL, a sortable table, CSV
download and Refresh (`POST /api/v1/analytics/query`). Saved answers open at `/insights?run=<id>`.

- **Checked figures.** Each number in `verification.numbers` is marked where it appears in the
  narrative: verified ones get a quiet dotted underline, unverified ones an amber mark, a
  warning icon, screen-reader text and an explanation below the text. The n-th occurrence of a
  text maps to the n-th entry with that text. Code spans are never marked.
- **Charts** are hand-written SVG (`analytics-chart.tsx`, geometry in
  `lib/analytics/chart-geometry.ts`): line, grouped bar, stacked bar. No charting dependency;
  three chart types did not justify one. The categorical palette (`--viz-1…8` in
  `globals.css`) was checked with the dataviz validator against the card surfaces: adjacent
  pairs pass the colour-blindness and normal-vision checks in light and dark. Three light slots
  are under 3:1 contrast on white, so every chart has a table view. More than 8 series fold
  into "Other". Hover or the arrow keys read one x at a time (with a live region).
- **Formats.** Tables show exact values (`€5,020,000`, `€1,234.56`); axes and labels are
  compact from 10,000 up (`€5.02M`, `€152K`). The contract does not say whether a `percent`
  value is a ratio or points, so a column whose values all lie within ±1 is read as ratios
  (0.42 → 42%), anything else as points.
- **Not covered by tests here:** the page against the real API. Unit tests mock `fetch`; the
  page was looked at in a browser against a local mock of the contract only.

## Docker

```bash
docker build -t workmind-web:dev .
```

The image runs `node server.js` as a non-root user on port 3000 and includes a `HEALTHCHECK`.
