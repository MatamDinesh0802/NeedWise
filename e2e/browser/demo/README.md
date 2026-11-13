# The demo video

`record.spec.ts` drives a real browser through NeedWise with captions drawn on the page, and
`render.mjs` turns the recording into the files the README and the docs use:

| File | What |
|---|---|
| `docs/media/demo.mp4` | H.264, 1280x720, about 1 minute 45 seconds: the whole walkthrough |
| `docs/media/demo.gif` | 960x540, about 27 seconds: a cited answer, then the same question without access |

The walkthrough, in eight captioned steps:

1. Alice (HR manager) signs in through Keycloak.
2. She asks for the L3 salary band; the answer cites Salary Bands 2026, and the citation opens
   the source beside it.
3. Bob (engineer), through **View as…**, asks the same question: no answer, no hint.
4. Bob asks a question that spans Engineering, Support and Operations; three department agents
   answer it.
5. Olivia (head of operations) asks, in **Research & act**, for this week's operations report by
   email; she edits the drafted email on its approval card and approves it.
6. A meeting: two of its decisions' timestamps are clicked, and the recording jumps there.
7. Olivia asks Insights for the monthly revenue trend: chart, explanation, checked figures, SQL.
8. Erin (IT admin), through **View as…**, creates a "Contractor onboarding agent" in
   Admin › Agents.

Everything the app shows is real: the stack, the model's answers, the timings. The page scripts
in `overlay.ts` only draw the captions, the title cards and a cursor (Playwright's video has no
cursor); nothing in the app is changed. `render.mjs` speeds up the waits (sign-ins 2 to 2.5x,
department agents 3x, the research run 8x, Insights 4x; the marks are in `out/marks.json`).

## Record it again

You need a running stack seeded with the Acme demo data (`make quickstart`), with:

- a chat model (`ANTHROPIC_API_KEY` in `.env`, [Quickstart](../../../docs/getting-started/quickstart.md));
- `ASSISTANT_MODE=auto` (or `agents`), so step 4 is answered by department agents; in the default
  `single` mode it is one search and the caption of step 4 is wrong;
- a meeting that Olivia can open: as `olivia.grant@acme.test`, upload
  `seed/acme/meetings/weekly-ops-sync-2026-11-02.m4a` (or the `.txt` transcript) to Operations
  SOPs in **Meetings**, and wait until it is ready;
- **View as…** turned on (`NEXT_PUBLIC_DEMO=1`, the default in `compose.yaml`);
- `ffmpeg` and `ffprobe` on the `PATH`.

Then, from the repository root:

```bash
make demo-video                                   # records, then renders docs/media/demo.*
E2E_BASE_URL=http://localhost:8680 make demo-video  # another stack
```

Or step by step, from `e2e/browser`:

```bash
pnpm install --frozen-lockfile && pnpm exec playwright install chromium
E2E_BASE_URL=http://localhost:8480 pnpm exec playwright test -c demo/playwright.config.ts
node demo/render.mjs            # out/raw.webm + out/marks.json -> docs/media/demo.mp4, demo.gif
DEMO_STOP_AFTER=4 pnpm exec playwright test -c demo/playwright.config.ts   # steps 1 to 4 only
```

| Variable | Default | |
|---|---|---|
| `E2E_BASE_URL` | `http://localhost:8480` | The stack, as a browser reaches it. |
| `DEMO_PASSWORD` | `needwise-demo` | The demo logins' password. |
| `DEMO_MAILPIT_URL` | `http://localhost:8025` | Mailpit, to delete the email step 5 sends. |
| `DEMO_STOP_AFTER` | `8` | Record steps 1 to N only, while working on the script. |

`render.mjs` stops with an error if the MP4 is over 20 MB or the GIF over 8 MB. The recording
of 2026-11-13: 146 seconds recorded, 104 seconds rendered, a 4.1 MB MP4 and a 1.2 MB GIF.

## What it costs and what it leaves behind

One recording makes five model requests (two answers, the department agents, the research run,
Insights): about $0.20 with Claude Sonnet 5.5, read from the usage each answer shows.

At the end, also when a step fails, the test deletes the four conversations, the agent it
created and the email in Mailpit, and rejects an approval that is still pending. It cannot
delete:

- the decided approval of step 5 (approvals have no delete: they are part of the audit trail);
- the Insights answer in Olivia's **Your answers** (there is no delete; the retention policy
  removes it);
- audit events and usage records, which are append-only by design.

Each recording therefore adds one approval and one Insights answer to the stack it ran on.
