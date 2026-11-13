# Browser end-to-end tests

Playwright (Chromium) and axe-core against a running, seeded stack, through Caddy and the real
Keycloak sign-in. It is its own package with its own lockfile, so `apps/web`'s dependencies are
untouched. The API's end-to-end tests (HTTP only) are in `apps/api/tests/e2e`.

## Run

```bash
make up seed          # the stack and the Acme Robotics demo data
make e2e-browser      # installs this package and Chromium, then runs the suite
```

Or by hand, from this directory:

```bash
pnpm install --frozen-lockfile && pnpm exec playwright install chromium
E2E_BASE_URL=http://localhost:8480 pnpm exec playwright test          # everything without a model
pnpm exec playwright test tests/knowledge.spec.ts                      # one file
E2E_MODEL=1 pnpm exec playwright test                                  # adds the @model tests
pnpm report                                                            # the HTML report
```

| Variable | Default | |
|---|---|---|
| `E2E_BASE_URL` | `http://localhost:8480` | The stack, as a browser reaches it (Caddy). |
| `E2E_MODEL` | unset | `1` also runs the tests tagged `@model`. They make paid model calls. |
| `DEMO_PASSWORD` | `workmind-demo` | The demo logins' password (the seed's `DEMO_PASSWORD`). |

## How it works

- **Sign-in** (`tests/support/auth.ts`): the header's "Sign in" link, Keycloak's email page, its
  password page, back to the app. Each user's storage state is cached in
  `.auth/<host>/<user>.json` (git-ignored: it holds session cookies; one folder per stack) and
  reused while `GET /api/v1/me` still answers for that user; otherwise the user signs in again. `auth.spec.ts` always signs in from scratch, in
  a fresh context, so its sign-outs never end a cached session.
- **Page guard** (`tests/support/fixtures.ts`): every test fails on a console error, an
  uncaught exception, a network failure, a 5xx, or a 4xx other than 401, 402 and 403 on the
  stack's origin. A test that provokes another status on purpose allows it for one URL (the
  404 a person gets for a collection they may not see).
- **Editions** (`tests/support/edition.ts`): Enterprise features are checked against
  `GET /api/v1/edition`. With a license that includes the feature the page shows it; without
  one, the upgrade notice. The same tests pass in Community (CI) and Enterprise. One test also
  answers the edition request as Community in the browser, so a licensed stack checks the
  Community side of every gate too.

## What it covers

| File | |
|---|---|
| `auth.spec.ts` | Sign-in and sign-out for erin (admin), hannah (finance manager), alice (HR manager), bob (engineer) and mallory (guest); each one's account menu and navigation (Admin only for erin; the others are refused at `/admin`); after sign-out the API answers 401 and Keycloak asks for the email again. A wrong password creates no session. |
| `smoke.spec.ts` | Every workspace page as every one of those users, every admin page as erin, and every admin page refused for the others: heading rendered, nothing loading, no error shown, guard clean. A collection, a document, a department, a group and a meeting (when there is one) reached by clicking. Every workspace page signed out. |
| `knowledge.spec.ts` | bob and hannah see different collections; the guest sees exactly one. A collection or document someone may not see is "not found", even with its link. hannah uploads a text file to Finance Reports, it is indexed (hash embeddings are enough), bob cannot open it, she deletes it. |
| `admin.spec.ts` | Agents (the editor, its collections and scope badges, the test console), Models, Budgets, Governance, Audit log (quick filters and the form change the URL; a URL fills the form; a date means local midnight), Retention. The Enterprise gates in both editions. The test console's rendering of a canned answer: markdown, citation chips, no image request, no `javascript:` link. |
| `work.spec.ts` | The approvals inbox and tasks tabs, each with items or its empty state; meetings; the Usage page says whose usage it shows: the whole organization (erin), Finance (hannah), only his own (bob). |
| `a11y.spec.ts` | axe-core (WCAG 2.2 A and AA, best practices) on the main workspace pages, seven admin pages, the agent editor, three pages in the dark theme and the signed-out overview. Serious and critical violations fail; all findings are attached to the report. |
| `layout.spec.ts` | No horizontal page scroll at 375 px and 1280 px on the main pages; on a phone the sidebar opens over the page and closes when a link is chosen. |
| `model.spec.ts` (`@model`) | A chat answer that cites a document, with the sources panel; an Insights chart and its table view; the agent test console with a real answer. |

### Accessibility exclusions

One finding is excluded, for one rule on one element (`EXCLUSIONS` in `a11y.spec.ts`): axe's
`label-content-name-mismatch` on the account menu button. Its name is "Account menu for
\<name\>"; its visible text is the avatar's initials and the name. axe counts the initials as
label text, so no name could contain it. The initials are an avatar, not a label, and the name
people would say is in the accessible name.

What axe found in the app was fixed instead: muted text on muted backgrounds at 4.34:1 (the
header avatar on every page), the Restricted scope badge at 3.19:1, and, from the layout tests,
the approvals tabs widening the page at 375 px and the phone sidebar staying open over the page
after a link was chosen. `apps/web/src/app/design-tokens.test.ts` keeps the colour tokens at
4.5:1 in both themes.

## Test data

Everything a test creates starts with "E2E browser" or is deleted by the test:

- the uploaded text file (`e2e-browser-<time>-<worker>.txt` in Finance Reports) is deleted
  through the UI, and through the API in a `finally` if the test failed before that;
- the `@model` chat test deletes its conversation;
- the `@model` Insights question starts with "E2E browser": analytics runs cannot be deleted
  through the API (the retention policy removes them).

What is not removed, by design: audit events (the log is append-only; every sign-in, upload and
deletion is recorded) and, for `@model` tests, the usage they record. Dialogs are opened and
cancelled; no setting is saved.

## CI

The `stack` job in `.github/workflows/ci.yml` runs this suite after the seed and the API
end-to-end tests: Community edition, hash embeddings, no model key, so the `@model` tests are
left out. On failure it uploads `playwright-report/` and `test-results/` (traces, screenshots).
With `CI` set, the suite runs on 2 workers and retries a failed test once.

What the suite assumes of the stack is the seed: the demo logins, the collections in
`seed/acme/company.yaml` and their documents, the seeded agents (HR, Engineering, Finance).
Approvals, tasks and meetings may be empty (CI has none).

## Measured (2026-11-12, when the suite was added)

- **CI's setup**, on a separate local stack built from the same commit (Community, hash
  embeddings with `RETRIEVAL_MIN_SIMILARITY=0`, no model key, seeded like CI): 159 of 159
  passed with `CI=1` (2 workers, retries allowed, none used) in 1.5 minutes from fresh
  sign-ins, and 159 of 159 again with cached sign-ins and through `make e2e-browser`.
- **The shared development stack** (Enterprise license, real embeddings), whose web image
  was built before the fixes listed above: 138 passed, 21 failed. The 21 are those findings:
  18 axe tests (contrast), the approvals tabs at 375 px, the phone sidebar, and the test
  console's markdown.
- No test file left a document behind on either stack. The `@model` tests have not been run
  yet: that run made no model calls.

## The demo video

`demo/` is not part of the suite: it records the captioned walkthrough in `docs/media/demo.mp4`
and the README's GIF (`make demo-video`; [demo/README.md](demo/README.md)). It reuses this
package's Playwright, so it adds no dependency.

## Not covered

Firefox and WebKit, visual comparison, connectors and their sync, uploading a meeting, chat
without a model (there is no answer to check), and anything that changes settings.
