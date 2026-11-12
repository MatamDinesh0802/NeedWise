# WorkMind Enterprise

WorkMind is open core ([ADR 0004](adr/0004-apache-2-open-core-and-dco.md),
[ROADMAP §4 and §5](ROADMAP.md#4-open-source-and-licensing-plan)). This page explains the two
editions, how a license turns Enterprise on, how to get a development license, and how
Enterprise code plugs into the core. The design is recorded in
[ADR 0011](adr/0011-open-core-boundary-and-license-keys.md).

## Community and Enterprise

**Community** is everything outside `ee/`, under the Apache License 2.0. It is the complete
product for a team: the assistant, permission-aware retrieval, agents, tools and MCP,
connectors, workflows and approvals, meetings, the BI agent, the evaluation suites. Security
basics are always in Community and never move to `ee/`: tenant isolation (forced Row-Level
Security), permission checks in code, SSO, scoped tokens, the hash-chained audit log.

**Enterprise** adds what large organizations need for compliance, scale and administration. Its
code lives in `ee/` under a separate commercial license ([`ee/LICENSE`](../ee/LICENSE)). It runs
only with a valid license key. The Enterprise features the core knows about:

| Feature name | What it is for | Status |
|---|---|---|
| `retention_policies` | Rules for how long data is kept before it is deleted | Built: [Retention policies](#retention-policies) |
| `department_budgets` | Spending limits on model usage per department | Built ([docs/usage.md](usage.md#enterprise)) |
| `chargeback_reports` | Usage and model costs per department, for internal billing | Built: `GET /api/v1/usage/export.csv` ([docs/usage.md](usage.md#enterprise)) |
| `advanced_audit_export` | Audit log exports for security tools and long-term retention | Built: [Audit export for a SIEM](#audit-export-for-a-siem) |

### What exists today

Built: license verification, the edition object, `GET /api/v1/edition`, the `require_feature`
dependency (402), the extension registry, the `workmind-ee` package, development licenses
(`make ee-dev-license`), the web components `EnterpriseGate` and `EnterpriseBadge`, and four
features, all registered in `workmind_ee.setup`: department budgets and the chargeback report
(`ee/python/src/workmind_ee/budgets.py` and `chargeback.py`, on the usage hooks), retention
policies and the SIEM audit export (`retention.py` and `audit_export.py`, as routers and a
scheduled task).

Not built: the rest of the paid column in the ROADMAP (permission sync from source systems,
SCIM, legal hold, DLP rules and the others have no feature name or code yet); official licenses
(no production signing key is published, so `PRODUCTION_PUBLIC_KEYS` is empty and only development
licenses exist); license revocation, online activation, seat counts; a screen to upload a
license. A valid license shows "enterprise" and its features in `/api/v1/edition` and turns on
the built features it lists; nothing else changes.

## Retention policies

Feature `retention_policies`, code in `ee/python/src/workmind_ee/retention.py`, screen
**Admin > Retention**. Per tenant, an org owner or admin sets how many days to keep:

| Data | Counted from | What is deleted |
|---|---|---|
| Conversations | The last message | The conversation and its messages |
| Insights questions (BI agent runs) | When they were asked | The run: question, SQL and answer |
| Meeting recordings | When the meeting was uploaded | The audio or video file only. The meeting, its transcript, summary, decisions and action items stay; the meeting shows that its recording was deleted, and its media URL answers `410` |
| Read notifications | When they were read | The notification. Unread ones are never deleted |

Every period is at least 30 days (checked by the API and by a database constraint) and at most
36,500. An empty period keeps that data forever; with no policy saved, nothing is deleted.

| Route | What it does |
|---|---|
| `GET /api/v1/admin/retention` | The policy, who saved it, and the last run |
| `PUT /api/v1/admin/retention` | Saves it (audited as `admin.retention.updated`, before and after) |
| `POST /api/v1/admin/retention/preview` | Counts exactly what a run would delete now, and the cutoff per kind. Deletes nothing. Without a body it previews the saved policy; with a policy in the body, that draft, without saving it |
| `POST /api/v1/admin/retention/run` | Runs the saved policy now. The body must be `{"confirm": "delete"}`; the web app asks the admin to type the word. Audited as `admin.retention.run` with the counts |

A scheduled task, `workmind_ee.retention.apply`, registered through the extension registry,
runs once a day and applies every active tenant's saved policy (audited the same way, with the
system as the actor). Each tenant's run happens inside that tenant's transaction, so row-level
security limits it to its own rows. Recording files are deleted only after the database
commit, only when their key is under the tenant's prefix, and only when no kept meeting and no
document still uses the same stored file (storage is content-addressed, so identical bytes are
stored once). The preview and the run use the same queries, and a test checks that the counts
match.

**Audit events are never deleted,** whatever the policy says. The log is append-only and
hash-chained: each event's hash covers the previous one, so removing old events would break the
verification of every later event. The database role the app runs as has no `DELETE`,
`UPDATE` or `TRUNCATE` on `audit_events` (migration `0002`). Long-term retention of the log
belongs in the SIEM that receives the export below.

Without the license, the four routes answer `402 enterprise_feature` (the core's stand-ins in
`api/v1/enterprise_fallbacks.py` know the paths), no task runs, and **Admin > Retention** shows
the Enterprise notice. The `retention_policies` table and the meetings' `media_deleted_at`
column exist in both editions (migration `0018`).

Not built: legal hold (keeping something past its period on purpose), retention of documents
and their versions, per-department or per-collection periods, and a dry-run report by
department.

## Audit export for a SIEM

Feature `advanced_audit_export`, code in `ee/python/src/workmind_ee/audit_export.py`.
`GET /api/v1/admin/audit-events/export.jsonl` (org owners, admins and auditors) streams the
tenant's whole hash chain, oldest first, one JSON object per line: the fields the hash covers
plus `prev_hash` and `hash` in hex. A receiver can verify the chain itself:

```text
hash = sha256(bytes.fromhex(prev_hash or "") + canonical(fields))
canonical = the JSON of the fields (without prev_hash and hash), keys sorted, separators "," and ":"
```

`after_seq` resumes an incremental pull (only events after that one). Events are read in
batches of 500, each in its own transaction. Each export is audited as `audit.export`. In the
web app the button is in **Admin > Audit log > Export**, inside an `EnterpriseGate`.

The Community edition keeps everything else: the chain itself, **Verify integrity**, the
explorer's filters and paging, and a CSV export of a filtered view (the newest 10,000 events);
see [governance.md](governance.md#the-audit-explorer).

## How licensing works

A license key is a token of two base64url parts, without padding:

```text
base64url(payload JSON) "." base64url(Ed25519 signature of the payload JSON bytes)
```

The payload:

```json
{
  "v": 1,
  "licensee": "Acme Robotics",
  "features": ["retention_policies", "chargeback_reports"],
  "issued_at": "2026-11-12T09:00:00Z",
  "expires_at": "2027-11-12T09:00:00Z",
  "deployment_id": "acme-prod"
}
```

`deployment_id` is optional. Unknown fields are refused, so a later format gets a new `v`
instead of being half-understood. Dates must carry a time zone.

The API verifies the signature with the `cryptography` library against the trusted public
keys (raw Ed25519, 32 bytes, base64url):

- `PRODUCTION_PUBLIC_KEYS` in `apps/api/src/workmind/core/edition.py`: the keys that will sign
  official licenses. Empty for now.
- A development public key (`LICENSE_DEV_PUBLIC_KEY` or `LICENSE_DEV_PUBLIC_KEY_FILE`), trusted
  **only when `APP_ENV=dev`**. In `test`, `demo` and `production` a development license is
  refused, and the log says so.

The deployment runs **Community** when there is no license, or when the license is malformed,
tampered with (payload or signature), signed by an unknown key, a development license outside
`APP_ENV=dev`, expired, dated in the future (5 minutes of clock skew are allowed), or issued for
another `DEPLOYMENT_ID`. Each of these logs a `license_rejected` warning with the reason. The
token itself is never logged or returned by the API.

Enterprise also needs the `workmind-ee` package to be installed (the Docker image has it). With
a valid license but no package, or when the package's `setup` raises, the process logs the
problem and runs as Community.

`Edition.has(feature)` reads the clock on every call: when a license expires while the API runs,
its features switch off at that moment (routes answer 402, scheduled tasks and hooks stop).
`/api/v1/edition` shows "community" from then on.

### Settings

| Setting | Meaning | Default |
|---|---|---|
| `LICENSE_KEY` | The license token itself | unset |
| `LICENSE_FILE` | A file that holds the token | `license.key` in the first of `/run/workmind/`, `<repo>/.workmind/` that has one |
| `LICENSE_DEV_PUBLIC_KEY` | The development public key | unset |
| `LICENSE_DEV_PUBLIC_KEY_FILE` | A file that holds it | `dev-public.key`, looked up like `license.key` |
| `DEPLOYMENT_ID` | This deployment's id, for licenses bound to one | unset |

`LICENSE_KEY` wins over `LICENSE_FILE`. compose mounts `<repo>/.workmind` read-only at
`/run/workmind` in `api`, `worker` and `beat`, so the defaults pick up a development license
without any change to `.env`.

### Which edition is running

- `GET /api/v1/edition` (any signed-in user):

  ```json
  {"edition": "community", "features": [], "licensee": null, "expires_at": null}
  ```

- The API's `startup` log line has `edition=community` or `edition=enterprise`.

### What the license is and is not

The license check is a contract mechanism, not copy protection. The `ee/` source is visible, and
someone who edits the code can remove the check; doing so breaks the commercial license, and no
code can prevent it. What the check guarantees is that an unmodified deployment runs Enterprise
code only with a license, and that the core runs completely without it.

## Development licenses

```bash
make ee-dev-license                                   # all features, 30 days
make ee-dev-license FEATURES="retention_policies chargeback_reports" DAYS=7
docker compose restart api worker beat                # pick it up
```

`make ee-dev-license` runs `uv run --extra ee wm license dev` in `apps/api` (options:
`--features`, `--days` 1 to 366, `--licensee`, `--deployment-id`, `--dir`). The first run
creates an Ed25519 key pair in `<repo>/.workmind/`; later runs reuse it. Each run writes:

| File | Content | Mode |
|---|---|---|
| `.workmind/license.key` | The signed license | 0644 |
| `.workmind/dev-public.key` | The public key the API trusts with `APP_ENV=dev` | 0644 |
| `.workmind/dev-signing.key` | The private key | 0600 |

`.workmind/` is in `.gitignore` and `.dockerignore`. The command never prints the token or the
private key. `make up` creates the folder when it is missing, so the bind mount always works.

To go back to Community, delete `.workmind/license.key` and restart the three services. For an
API run on the host (`make api-dev`), install the package first (`make install` runs
`uv sync --extra ee`); the settings find `<repo>/.workmind/` on their own.

The whole folder is mounted, so on Docker Desktop the containers can read the private key too
(on Linux the 0600 mode keeps the container user out). It signs only development licenses,
which no environment other than `dev` accepts.

## Extension points

Enterprise code is the `workmind-ee` package in `ee/python/` (module `workmind_ee`). It imports
from the core; **the core never imports from it**. `tests/test_open_core_boundary.py` checks
this in two ways: it parses every core module for imports of `workmind_ee`, and it starts a process
that imports every core module and builds the app, then checks that `workmind_ee` was never
loaded.

The package declares an entry point:

```toml
[project.entry-points."workmind.ee"]
workmind_ee = "workmind_ee:setup"
```

At startup (the API in `create_app`, Celery workers and beat when they import
`workmind.workers.celery_app`) the core verifies the license. Only with a valid one does it load
the `workmind.ee` entry points and call `setup(registry, edition)`. Setup is all or nothing: if
it raises, the core logs `enterprise_setup_failed`, drops everything registered and runs as
Community.

Every registration names the licensed feature it belongs to, and the core checks that feature
on every request, task run and hook call:

```python
from fastapi import APIRouter

from workmind.core.edition import Edition
from workmind.core.extensions import ExtensionRegistry
from workmind.core.resources import Resources

router = APIRouter(prefix="/admin/retention")


@router.get("")
async def list_policies() -> list[dict[str, str]]: ...


async def apply_policies(resources: Resources) -> int: ...


def setup(registry: ExtensionRegistry, edition: Edition) -> None:
    # Mounted under /api/v1, behind sign-in and require_feature("retention_policies").
    registry.add_router(router, feature="retention_policies")
    # Queued by Celery beat every hour; skipped while the feature is not licensed.
    registry.add_scheduled_task(
        "workmind_ee.retention.apply", apply_policies, every_s=3600, feature="retention_policies"
    )
```

**Routers** are mounted under `/api/v1` after the core routes, so they can never shadow one.
Each route needs sign-in first (401) and then the feature (402). Routes still check permissions
themselves, as every core route does (`require_scope`, organization roles, OpenFGA).

**`require_feature(name)`** (`workmind.api.deps`) can also gate any single route:

```python
@router.get("/chargeback", dependencies=[require_feature("chargeback_reports")])
```

Without the feature it answers:

```json
{
  "type": "about:blank",
  "title": "Enterprise feature",
  "status": 402,
  "code": "enterprise_feature",
  "detail": "Chargeback reports ('chargeback_reports') is part of WorkMind Enterprise and is not licensed on this deployment. To enable it, install a license that includes it: see docs/enterprise.md."
}
```

**Scheduled tasks** have names that start with `workmind_ee.` (so they can never replace a core
task), run at most once every 10 seconds, and receive fresh `Resources` that are closed
afterwards.

**Hooks** are named extension points the core declares where it wants Enterprise behaviour:

```python
from workmind.core.extensions import Hook

# in a core module (workmind/modules/usage/extensions.py)
CHARGEBACK_REPORT = Hook[[AsyncSession, ChargebackQuery], Awaitable[str]]("usage.chargeback_report")
# in setup (workmind_ee/__init__.py)
registry.add_hook(CHARGEBACK_REPORT, chargeback_csv, feature="chargeback_reports")
# where the core calls it (api/v1/usage.py)
reports = resources.extensions.call(CHARGEBACK_REPORT, session, query)  # [] in Community
```

`call` runs the licensed implementations in registration order and returns their results
(an empty list in Community). Exceptions propagate to the caller, so a hook that guards
something cannot fail open. Hooks compare by identity: use the core's constant. A hook is
declared next to the code that calls it. The core declares two, both in
`modules/usage/extensions.py`: `usage.department_budgets` (returns the object that decides which
departments a budget may name, binds, shows and warns) and `usage.chargeback_report` (returns
the CSV). The route that serves a feature stays in the core behind `require_feature`, so it
answers 402 in Community instead of 404.

**The edition** is on `resources.edition` (and `app.state.extensions.edition`); ask
`edition.has("feature")`, never `edition.enterprise`, so each feature follows its own license.

**Database tables** for Enterprise features are created by the core's Alembic migrations
(`apps/api/migrations`), with forced Row-Level Security like every tenant table. There is one
schema for both editions, so adding or removing a license never needs a migration.

**Web.** Screens for Enterprise features live in `apps/web` and are wrapped in
`EnterpriseGate` (`@/components/enterprise/enterprise-gate`), which shows them when the feature
is licensed and otherwise a short note ("Enterprise feature: …; see docs/enterprise.md").
`EnterpriseBadge` marks Enterprise-only UI. `@/lib/edition` has the client, `hasFeature` and
`isEnterpriseFeatureError` for the 402. All of this is cosmetic: the API enforces the license.

## Building and testing

The API image installs the `ee` extra, so one image runs either edition. compose passes `ee/`
as the named build context `ee`; by hand:

```bash
docker build --build-context ee=ee apps/api
```

Backend tests run with the package installed (`uv sync --extra ee`, as CI does); the tests in
`ee/python/tests` are collected from `apps/api` and skip without it. Relevant tests:
`tests/test_edition.py` (tokens, keys, expiry, settings), `tests/test_extensions.py` (setup
isolation, hooks, mounting), `tests/test_enterprise_tasks.py`, `tests/test_dev_license.py`,
`tests/test_open_core_boundary.py`, `tests/integration/test_edition_api.py`
(`/edition` is Community without a license; a gated route answers 402),
`tests/integration/test_retention.py` (every route 402 without a license; with one, the 30-day
minimum, exact preview counts, a run that deletes only its own tenant's expired rows and files,
the daily task) and `tests/integration/test_audit_explorer.py` (the SIEM export is Enterprise
only, and its chain verifies). Lint `ee/python` with
`make lint`, which runs ruff on it too; pyright checks it in strict mode.
