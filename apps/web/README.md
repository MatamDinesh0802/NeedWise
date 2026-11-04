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

## Docker

```bash
docker build -t workmind-web:dev .
```

The image runs `node server.js` as a non-root user on port 3000 and includes a `HEALTHCHECK`.
