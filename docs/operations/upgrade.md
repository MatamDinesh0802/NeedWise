# Upgrades

How to upgrade a WorkMind deployment run with Docker Compose, what runs during an upgrade,
every manual step from v0.1.0 to this code, what the tested upgrade measured, and how to roll
back. Backups are in [backup-restore.md](backup-restore.md).

## Supported paths

- **From any release to a later one, directly.** The database migrations are one linear chain
  (`0001` to `0019` today) and the `migrate` service applies every missing step in order, so
  there is no need to stop at intermediate versions.
- **Tested:** v0.1.0 (the only tag so far, 2026-11-04; alembic `0004`) to this code (alembic
  `0019`), four times on 2026-11-12, with `scripts/upgrade-test.sh`
  ([below](#measured-v010-to-this-code)). Restoring a v0.1.0 backup into this code, and rolling
  an upgraded stack back to v0.1.0, were tested too.
- **Not supported:** downgrades (the migrations go forward only; to go back, restore the backup
  you took before upgrading), and running the old and the new version side by side (a rolling
  upgrade). Plan a short downtime: from `docker compose up` to healthy took 32 to 138 seconds
  in the test runs, depending on how busy the machine was (the migrations themselves take
  seconds); build the images before stopping the old version to keep builds out of it.

## How an upgrade runs

- **Migrations.** Alembic, in `apps/api/migrations/versions/`, revision ids are plain numbers.
  The one-shot `migrate` service runs `alembic upgrade head` as `workmind_owner` (the schema
  owner; the API itself runs as `workmind_app`, which can never bypass Row-Level Security).
  `api`, `worker` and `beat` wait for it to finish successfully; if it fails, nothing starts and
  `docker compose logs migrate` says why. Each migration that adds a tenant table also forces
  Row-Level Security on it with its `tenant_isolation` policy.
- **Authorization model.** Each tenant's OpenFGA store holds a copy of the model. Seeding wrote
  it, and nothing else did until `wm authz sync`: run it after every upgrade
  (`docker compose run --rm migrate wm authz sync`). It writes the current model where it
  differs and recomputes the tuples from PostgreSQL. The model changed once since v0.1.0: the
  document type got its own access lists ([ADR 0012](../adr/0012-document-access-lists.md),
  migration `0022`). Until `wm authz sync` has written it, OpenFGA refuses the new document
  tuples: the outbox retries them (later changes wait behind them for up to about 20 seconds,
  then go ahead) and the sync writes everything. Retrieval is not affected meanwhile: lock 2
  checks documents only when they have their own list, and none has one before the upgrade.
- **Keycloak.** Keycloak migrates its own database when its image version changes.
  `deploy/keycloak/realm-workmind.json` is imported once, when the realm does not exist yet:
  later edits to that file are not applied to an existing realm, so a release that changes it
  must say what to change by hand. It has not changed since v0.1.0.
- **Qdrant.** The collection is named after the embedding model and its size
  (`chunks_<model>_<dimensions>`, e.g. `chunks_embeddinggemma_300m_768`). Changing
  `EMBEDDING_MODEL` or `EMBEDDING_DIMENSIONS` points WorkMind at a new, empty collection: run
  `docker compose run --rm worker wm reindex <tenant slug>` for each tenant, then delete the old
  collection. (The `chunks_active` alias in ARCHITECTURE §8 is not built.)
- **Infrastructure images** (Postgres 18.6, Valkey 9.1.3, Qdrant 1.20.0, OpenFGA 1.22.1,
  Keycloak 26.8.1, Caddy 2.11.7) are the same in v0.1.0 and this code. When a release changes
  one, its notes say so: Postgres major versions in particular need `pg_upgrade` or a dump and
  restore, never just a new image on the old volume.

## Procedure

In the deployment directory (a git checkout; `.env`, `.data/` and `.workmind/` are not tracked
and stay):

1. Read the release notes of every version you skip.
2. `git fetch --tags && git checkout v<new>`, then `docker compose build`. The running
   containers are not affected; this only keeps the build out of the downtime.
3. **Back up:** `make backup` (or `scripts/backup.sh --offline`, so nothing changes after it).
   Keep it until you have checked the upgraded system.
4. `docker compose down` (**without** `-v`: the volumes hold your data).
5. Make the [manual changes](#from-v010-manual-steps) for your path, in `.env`.
6. `make up` (= `make objects-dir && docker compose up -d --build --wait`). The
   migrations run before the application starts.
7. `docker compose run --rm migrate wm authz sync`.
8. Check: `make status`, sign in, search for something you know is there.

**With released images** (`WORKMIND_API_IMAGE` and `WORKMIND_WEB_IMAGE` set to the published
images, digest-pinned as [releasing](releasing.md) recommends), nothing is built: step 2 is
`docker compose pull` after setting the new version's images in `.env`, and step 6 is
`make objects-dir && docker compose up -d --no-build --wait`. Do not run `docker compose build`
or `make up` there: with a digest in the image name they fail ("build tag cannot contain a
digest"); with a tag only, they replace the released image, which was scanned and passed the
permission gate, with a local build under the same tag.

`scripts/upgrade-test.sh <from tag>` runs exactly these steps on a throwaway copy, if you want
to try an upgrade before doing it (it needs the old tag in your git history).

## From v0.1.0: manual steps

v0.1.0 was identity, tenancy and permissions only (departments, groups, people, access tokens,
the audit log). Everything since (knowledge, the assistant, agents, connectors, workflows,
meetings, BI, governance, usage, editions) arrives with the upgrade. What you change:

| What | Why | Needed for |
|---|---|---|
| `SECRETS_ENCRYPTION_KEY` (32 random bytes, base64; see `.env.example`) | Encrypts tenants' own model provider keys. Outside `dev`/`test` the API refuses to start without it, or with the development value. | `APP_ENV=production` or `demo` |
| `SECRETS_KEY` (32 random bytes, base64) | Encrypts connector credentials. Refused in production when unset. | `APP_ENV=production` |
| An embedding server: `EMBEDDING_BASE_URL` (+ `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` if not EmbeddingGemma 768) | Uploads are embedded, searches too. Default: `http://host.docker.internal:8091/v1` (`make embeddings` on a Mac); or `COMPOSE_PROFILES=embeddings` with `EMBEDDING_BASE_URL=http://embeddings:8080/v1` for the CPU container. | Documents and search |
| A language model: `ANTHROPIC_API_KEY`, or `LLM_PROVIDER=openai_compatible` and `LLM_BASE_URL` ([docs/models.md](../models.md)) | Answers, agents, meeting summaries, BI. Search works without it. | Answers |
| Outgoing mail: `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, ... | Compose sends development mail to Mailpit; set a real server in production. | Email |
| Directories `.data/objects` and `.workmind` | New bind mounts: object storage, and license files (read-only). `make up` creates them (`make objects-dir`). On Linux, `.data/objects` must be writable by uid 10001, the containers' user: `make objects-dir` hands it over when it is not. | Uploads; Enterprise |

Nothing else needs doing by hand on this path:

- **Keycloak:** same realm file, same Keycloak version; existing logins keep working (the
  upgrade test signed in four people created by v0.1.0's seed).
- **OpenFGA:** same model; `wm authz sync` wrote 0 tuples and removed 0.
- **Qdrant:** v0.1.0 had no collection; the first upload creates it.
- **New services** (`worker`, `beat`, `mailpit`, the optional `embeddings`) and **volumes**
  (`beat_schedule`, `whisper-models`, `llama-models`) are created by `docker compose up`.
- **The API image** now also contains the Enterprise package (inactive without a license);
  compose passes it as the `ee` build context (`docker build --build-context ee=ee apps/api` by
  hand).
- **Data:** a tenant created on v0.1.0 starts with no knowledge collections or agents. An admin
  creates collections (the upgrade test did, for a department made on v0.1.0).

## Measured: v0.1.0 to this code

On 2026-11-12, `scripts/upgrade-test.sh v0.1.0` on one laptop (Apple silicon, Docker Desktop
with 8 CPUs and 16 GB, shared with other running stacks), with the host's embedding server, as
compose project `workmind-upgrade` on ports offset by 200 (`http://localhost:8680`). Image builds
used Docker's build cache from earlier builds on the same machine; a cold build was not timed.

The old version: v0.1.0 (`0.0.1` in its package metadata), alembic `0004`, seeded with its own
Acme seed (20 people, 8 departments, 3 groups). Created through its API: a department
`upgrade-drill` and a group `upgrade-testers`, both with bob as a member, and a personal access
token for bob (`profile:read`, `knowledge:read`). v0.1.0 had no conversations or uploads, so the
test could not create any before upgrading.

| Run | Old: build, start and seed | Backup | New: build | New: `up` to healthy (migrations included) | Checks | Whole test |
|---|---|---|---|---|---|---|
| 1 | 67 s, 52 s | 3 s | 13 s | 32 s | 16 of 17; the usage check was wrong (it left out the required dates), fixed, then 17 of 17 on the same stack | not timed |
| 2 | 10 s, 52 s | 2 s | 14 s | 71 s | 17 of 17 | 172 s, teardown included |
| 3 | 10 s, 67 s | 4 s | 45 s | 138 s | 16 of 17: the API's status check reported Keycloak down (its 2-second timeout, right after start on a busy machine) while sign-ins worked; the checks now retry the status for up to 90 s | kept for the rollback |
| 4 (the scripts as committed) | 14 s, 64 s | 3 s | 30 s | 96 s | 17 of 17 | 219 s |

What the checks covered, after the upgrade:

| Check | Result |
|---|---|
| Alembic | `0004` -> `0019`, the code's head |
| Rows | 10 tables before, 35 after; no table lost a row |
| System status | ok: Postgres, Valkey, Qdrant, OpenFGA, Keycloak |
| Sign-in (erin, bob, alice, mallory; users of v0.1.0's seed) | all four |
| Bob's personal access token, created on v0.1.0 | accepted (`GET /api/v1/me`), and searched with it |
| Department `upgrade-drill` and group `upgrade-testers`, created on v0.1.0, with bob in both | kept |
| Audit log hash chain | intact, over the v0.1.0 events and the new ones |
| `GET /api/v1/edition` (new) | `community` (no license) |
| `GET /api/v1/usage/summary` (new) | answered |
| A collection for the v0.1.0 department, an upload by bob (new) | indexed (`ready`) |
| Search with bob's v0.1.0 token for the uploaded text | the uploaded document first |
| Permission gate: alice (another department) and mallory (guest) | neither found the document; alice was not shown the collection |
| Conversations (new) | created and listed |

**Restoring a v0.1.0 backup into this code** (the backup the test takes before upgrading,
restored with `scripts/restore.sh --force --public-url ...` into a stack running this code),
twice:

- into run 4's upgraded stack: 78 s, of which 28 s for the start with migrations `0004` ->
  `0019`; exit code 0 (rows, objects and system status as in the manifest), then the same 17
  checks passed;
- into the restore-drill stack of [backup-restore.md](backup-restore.md#measured-a-restore-drill):
  56 s; the same checks passed except the edition, which said Enterprise because that target
  already had a license file (a restore adds or replaces license files, it never deletes one).

## Rollback

- **Before the migrations ran** (the new version failed to build or start): `docker compose
  down`, `git checkout v<old>`, `make up`.
- **After they ran:** there is no downgrade migration. Restore the backup from step 3 with the
  old version:
  1. `docker compose down` **while the new version is checked out** (the old compose file does
     not know the new services, so it would leave `worker` and `beat` running).
  2. Copy `scripts/` somewhere (v0.1.0 has no restore script), then `git checkout v<old>`.
  3. `DEPLOY_DIR=$PWD <copy>/scripts/restore.sh <backup> --force`. It starts only Postgres,
     Valkey and Qdrant before replacing the databases, so the old `migrate` never meets the
     newer schema. On a version without `wm authz sync` (v0.1.0) that step only warns.
  4. The volumes only the new version used (`beat_schedule`, `whisper-models`) stay behind;
     `docker volume rm` them when you no longer plan to upgrade.

  Tested on run 3's upgraded stack: back on v0.1.0's files and images, the restore took 253 s
  (187 s of it the start, on the busy machine); alembic was `0004` again, erin, bob and mallory
  signed in, bob's token was accepted, the `upgrade-drill` department was there and the audit
  chain verified (12 events). What was created after the backup (the collection, the upload,
  the conversation) was gone, as expected.
- Data created between the backup and the rollback is lost; that is the price of a forward-only
  schema. Keep the window short: back up, upgrade, check, and only then reopen.

## Kubernetes

The Helm chart follows the same order
([Kubernetes](../deploy/kubernetes.md#upgrades-and-the-migrate-hook)): its `migrate` hook Job
runs `alembic upgrade head` with the owner role before the new `api`, `worker` and `beat` pods
start, its `authz-sync` hook Job runs `wm authz sync` once they are ready, and beat is always
one replica. Back up first ([backups](../deploy/kubernetes.md#backups)). An upgrade from one
WorkMind version to another has not been run on Kubernetes: the chart's test on kind upgraded
a release to the same build ([tested on a cluster](../deploy/kubernetes.md#tested-on-a-cluster)).
