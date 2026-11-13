# Backup and restore

How to back up a NeedWise deployment run with Docker Compose, how to restore it (on the same
host or another one), and what a restore drill on 2026-11-12 measured. The scripts are
`scripts/backup.sh` and `scripts/restore.sh` (`make backup`, `make restore`). Upgrades are in
[upgrade.md](upgrade.md).

What is built: backup and restore of the Compose stack, tested end to end on one laptop (below).
What is not built: point-in-time recovery (WAL archiving), incremental backups, encryption inside
the scripts, and anything Kubernetes-specific (see [Kubernetes](#kubernetes)).

## What a backup holds

`scripts/backup.sh` writes one directory, `backups/needwise-<UTC time>/` (mode 0700, files 0600):

| Part | How | File |
|---|---|---|
| NeedWise's database (`needwise`) | `pg_dump --format=custom`, from an exported snapshot | `postgres/needwise.dump` |
| Keycloak's database (the realm, users and their password hashes, NeedWise's OIDC client and its secret) | `pg_dump -Fc` | `postgres/keycloak.dump` |
| OpenFGA's database (authorization stores and tuples) | `pg_dump -Fc` | `postgres/openfga.dump` |
| Roles (`needwise_owner`, `needwise_app` with `NOBYPASSRLS`, `keycloak`, `openfga`) | `pg_dumpall --globals-only --no-role-passwords`: no password hashes | `postgres/globals.sql` |
| Qdrant: every NeedWise collection (`chunks_<model>_<dimensions>`) | a snapshot through Qdrant's API, downloaded, then deleted inside Qdrant | `qdrant/<collection>.snapshot` |
| Object storage (uploads, meeting recordings, analytics DuckDB files) | `tar -czf` of exactly the files listed in `objects.sha256` | `objects.tar.gz`, `objects.sha256` |
| License | `license.key`, and `dev-public.key` for a development license | `license/` |
| What it all is | see below | `manifest.json`, `SHA256SUMS` |

`manifest.json` records: when the backup started and ended and how (mode, services stopped);
the app version, git commit and API image; the alembic revision; the embedding model, its
dimensions and the Qdrant collection; each database and its owner; the exact row count of every
table in NeedWise's schema and the documents per status; each collection's point count and
vector size; the number of objects; the size and SHA-256 of every file. `SHA256SUMS` works with
`shasum -a 256 -c SHA256SUMS` (or `sha256sum -c`).

### Not in a backup

- **Valkey.** It holds sessions, sign-in flows in progress, the Celery job queue, live run
  events, conversation locks and caches (usage totals, budget alert flags). After a restore it
  is emptied: everyone signs in again; jobs that were queued but not started are gone. A
  document uploaded just before the backup stays `pending`: the restore counts them, and
  `nw reindex <tenant>` indexes every document of the tenant again, inline (it does not
  re-queue jobs). A meeting that was `pending`, `transcribing` or `extracting` (uploaded during
  the backup, or cut off when `--pause` stopped the worker) stays in that state: the restore
  lists them, and nothing re-queues a meeting (there is no retry endpoint or command), so delete
  it and upload it again. An answer being written at backup time is lost; a budget alert may be
  sent a second time. Nothing in Valkey is the only copy of anything a person made.
- **The beat schedule volume.** It only remembers when each periodic job last ran. Without it,
  every interval starts again when beat starts (a daily job runs within a day).
- **Model caches** (`whisper-models`, `llama-models`): downloaded again on first use.
- **Secrets**: `.env`, `SECRETS_ENCRYPTION_KEY`, `SECRETS_KEY`, `OIDC_CLIENT_SECRET`, the
  database passwords, `OPENFGA_API_KEY`. Keep them in a password manager or secret store, apart
  from the backups (see [Secrets](#secrets-and-the-license)).
- **`.needwise/dev-signing.key`**, the private key of development licenses.

## Taking a backup

```bash
make backup                                  # = scripts/backup.sh, into ./backups
scripts/backup.sh --output /srv/backups      # elsewhere
scripts/backup.sh --keep 7                   # and delete all but the newest 7
```

It needs `docker` (with compose v2), `python3` (3.9 or later, standard library only) and the
Qdrant port published on the host (`QDRANT_PORT`, as in `compose.yaml`). Postgres tools run
inside the `postgres` container, so nothing else is installed on the host. Environment
variables: `DEPLOY_DIR` (default: the repository), `COMPOSE_PROJECT_NAME` / `COMPOSE_FILE`,
`QDRANT_URL`, `QDRANT_API_KEY`, `API_URL`, `OBJECTS_DIR`, `LICENSE_DIR`, `PG_DATABASES`,
`QDRANT_INCLUDE` / `QDRANT_EXCLUDE` (regular expressions; the default leaves out the
`chunks_test_*` collections integration tests create).

**A Postgres you cannot `exec` into** (a managed database, another host): set the standard
libpq variables `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD` and the tools run in a throwaway
`postgres:18.6` container over TCP instead. `--no-compose` additionally keeps the script away
from `docker compose` entirely (online backups only).

`PGUSER` must be a **superuser or a role with `BYPASSRLS`**. Every NeedWise table has `FORCE
ROW LEVEL SECURITY`, and `pg_dump` reads with row security off, which Postgres allows only to
such roles: the table owner (`needwise_owner`), and an admin that is a member of it (as
[Kubernetes](../deploy/kubernetes.md) suggests for creating the roles), get `query would be
affected by row-level security policy` and the dump fails; under RLS the manifest's row counts
would also read 0. The script checks the role first and refuses with that explanation. A
restore needs the same: Postgres refuses `COPY FROM` into a table with row-level security for a
role that does not bypass it (not tried here). If your managed service lets you create no such
role, use its own snapshots for Postgres.

### Consistency

A backup reads three stores that change independently, so the script does three things:

1. **One snapshot for NeedWise's database.** A `psql` session opens a `REPEATABLE READ`
   transaction and exports its snapshot; `pg_dump --snapshot` dumps exactly that, and the row
   counts in the manifest are counted in the same transaction. The manifest therefore
   describes the dump exactly, even while people work. The Keycloak and OpenFGA databases are
   dumped one after the other, each consistent in itself. OpenFGA is a projection of NeedWise's
   tables ([ARCHITECTURE §7.5](../ARCHITECTURE.md#75-source-of-truth-and-synchronization)), so
   the restore recomputes it from PostgreSQL (`nw authz sync`) and any difference between the
   two dumps disappears.
2. **Objects pinned before the database, archived after it.** The API keeps deleting objects
   during a `--pause` or `--online` backup (a deleted document or meeting, the previous version
   of a replaced analytics dataset). So before the snapshot the script hard-links every object
   into `.data/objects/.backup-pin-<backup>/` (objects are never rewritten in place: a link
   keeps a deleted file's content), links the new ones again after the dump (an upload writes
   its object before the row that points at it commits), and archives the pins in one pass that
   reads, hashes and stores each file. Every object the dump refers to is in the archive;
   deleted or newer ones are harmless extras. Temporary files (`*.tmp` uploads,
   `.<version>.building.duckdb` datasets) are left out. The pins are removed at the end.

   Where hard links are not possible (the backup user may not link the files, e.g. on Linux
   when it is neither root nor uid 10001, or the tree spans filesystems) the script says so and
   archives the live tree: an object deleted between the snapshot and the archive is then
   missing from the backup, and the restored row that refers to it has no file (its download
   fails, `nw reindex` counts it as failed). Use `--offline`, or run the backup as root, when
   that matters. Either way a file deleted while it is archived is skipped instead of failing
   the backup (before this was fixed, `tar` exited 1 or 2 and the backup died).
3. **Background writers paused.** By default (`--pause`) the script stops `worker` and `beat`
   (up to `STOP_TIMEOUT`, 120 s, for running jobs to finish; Celery acknowledges a job only
   when it is done, so an interrupted one runs again) and starts them again at the end, also on
   failure. Ingestion, connector syncs and scheduled jobs (the main writers of Qdrant points and
   objects) wait; people keep working and their uploads queue up.

What remains possible in `--pause` mode: a change made through the API between the database
dump and the Qdrant snapshot (a document deleted, a collection's sharing changed) is in the
snapshot but not in the dump. Search still checks every passage against OpenFGA, rebuilt from
the dump, so this can hide a passage, never show one to someone who may not see it. When that
matters, use `--offline`: it also stops `api` and `web`, so nothing changes (in the drill below
they were stopped for 18 seconds, and the API then needs about 10 seconds to start). `--online`
stops nothing.

### Schedule and retention

- **Daily** at a quiet hour, with `--pause`: `0 2 * * * cd /srv/needwise && scripts/backup.sh --keep 7 >>/var/log/needwise-backup.log 2>&1`.
  A failed run removes its unfinished directory (it may already hold full database dumps,
  which `--keep` would never expire: it rotates only complete backups) and exits non-zero:
  alert on that. `--keep` takes a plain number.
  Your RPO is then up to 24 hours. At the drill's size a backup takes 8 to 10 seconds, so
  hourly is practical too.
- **Off the host**, encrypted (below): keep, for example, 7 daily, 4 weekly and 12 monthly
  copies. Your legal retention rules may say otherwise; the backup keeps data a retention
  policy later deleted, until the backup itself expires.
- **Test restores** regularly, into a separate stack (the [drill](#measured-a-restore-drill)
  shows how); a backup that was never restored is a hope, not a backup.
- Watch the exit code: 0 means written and self-checked against its checksums.

### Encryption at rest

The backup is not encrypted. It holds everything: every document (the raw files, and their text
in the dumps and the Qdrant payloads), Keycloak's password hashes and its copy of the OIDC
client secret, tenants' model provider keys (encrypted with `SECRETS_ENCRYPTION_KEY`) and
connector credentials (encrypted with `SECRETS_KEY`). Encrypt it before it leaves the host, for
example with [age](https://age-encryption.org):

```bash
tar -C backups -cf - needwise-20261112T171353Z | age -r age1...recipient > needwise-20261112T171353Z.tar.age
age -d -i key.txt needwise-20261112T171353Z.tar.age | tar -C /restore -xf -
```

or send it to object storage with server-side encryption under your KMS key. Keep the
decryption key apart from the backups and from `.env`.

### Secrets and the license

| Secret | Protects | If it is lost |
|---|---|---|
| `SECRETS_ENCRYPTION_KEY` (+ `_PREVIOUS_KEYS`) | tenants' own model provider keys (AES-256-GCM, [docs/models.md](../models.md)) | Restored keys cannot be decrypted: every tenant enters its provider keys again. Rotation needs the old key ([`nw secrets rotate`](../models.md)). |
| `SECRETS_KEY` | connector credentials (Google Drive tokens, ...) | Each source is connected again. |
| `OIDC_CLIENT_SECRET` | the API's login to Keycloak | Keycloak's database (restored) holds the source's value: set the same value on the target, or change both. |
| `OPENFGA_API_KEY` | calls from the API to OpenFGA | Not stored in any database: any value, the same in both places. |
| Database passwords | the roles | Not in the backup: the target's roles keep their own passwords. |
| Keycloak admin password | the `master` realm | Restored with Keycloak's database: after a restore, the source's admin login applies. |

Outside `dev` and `test` the API refuses to start without `SECRETS_ENCRYPTION_KEY`; in
development both encryption keys fall back to fixed development values, which is why a dev
backup restores anywhere.

**License.** `license.key` is in the backup (and `dev-public.key` for development licenses).
Keep the token you received too. A license issued for one deployment (`DEPLOYMENT_ID`) is valid
only where that `DEPLOYMENT_ID` is set: set the same value on a replacement host, or ask for a
new license.

## Restoring

```bash
make restore BACKUP=backups/needwise-20261112T171353Z
scripts/restore.sh backups/needwise-20261112T171353Z --force --public-url https://needwise.example.com
```

Into the compose project of `DEPLOY_DIR`, with the images that project runs. In order:

1. Checks every file against `SHA256SUMS`, and asks the API image for its alembic head: a
   backup **newer** than the code is refused (restore it with that version or later).
2. **Refuses a target that holds data** (any tenant, any Qdrant point, any object) unless
   `--force` is given, and lists what it found (exit code 2).
3. Stops everything except `postgres`, `valkey` and `qdrant`.
4. Applies `globals.sql` (roles that already exist report "already exists"; attributes are set
   again; passwords stay the target's), then for each database `DROP DATABASE ... WITH (FORCE)`
   and `pg_restore --create --exit-on-error` (owner, encoding and locale come from the dump).
   Compares NeedWise's row counts with the manifest, table by table.
5. Uploads each Qdrant snapshot (`priority=snapshot`, with its SHA-256 for Qdrant to check),
   which replaces the collection, and compares the point counts. A NeedWise collection the
   backup does not have (another embedding model's, say) is deleted: the target becomes the
   backup.
6. Moves an existing object tree aside (`.data/objects.before-restore-<time>`, not deleted),
   extracts the archive and checks every object's SHA-256. On Linux it gives the tree to uid
   10001, the user the containers run as. Restores the license files (a different existing file
   is kept as `*.before-restore-<time>`; a license file the backup does not have is left alone).
7. Empties Valkey (`FLUSHALL`).
8. `docker compose up -d --wait`: the `migrate` service runs `alembic upgrade head` first, so a
   backup **older** than the code is migrated here. Prints the revision before and after.
9. With `--public-url`: points Keycloak's client at the new address (below).
10. `docker compose run --rm migrate nw authz sync`: every tenant's OpenFGA store gets the
    current model and the tuples recomputed from PostgreSQL.
11. Reads `/api/v1/system/status` and names documents that were `pending` or `processing`.

Exit codes: 0 restored and every check matched; 1 failed; 2 refused; 3 restored, but a check
differs from the manifest (printed above it). `--no-start` stops after step 7.

### On another host

1. Install Docker, check out the **same release or a newer one**, and copy `.env` from your
   secret store (the encryption keys and `OIDC_CLIENT_SECRET` must be the source's; database
   passwords can be new).
2. `make objects-dir && docker compose up -d --wait` once: Postgres creates its roles and
   databases (`deploy/postgres/init`), Keycloak imports an empty realm.
3. Copy the backup over (and decrypt it), then `make restore BACKUP=<dir>`. The fresh stack
   holds no tenant, so `--force` is not needed. The restore refuses a target whose embedding
   settings (`EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`) name another search index than the
   backup's: it would restore a collection the API never searches and remove the one it does.

### On another address

Keycloak keeps the address people sign in through in its database: NeedWise's client has the
redirect URI `<PUBLIC_URL>/api/auth/callback` and the post-logout redirect `<PUBLIC_URL>/*`,
written when the realm was first imported. A restore brings back the source's values, so on a
new address sign-in stops on Keycloak's page "Invalid parameter: redirect_uri". The drill below
showed exactly that. What an operator changes:

1. `PUBLIC_URL` (and `HTTP_PORT` if it changes) in `.env`, then `docker compose up -d`: this
   sets Keycloak's hostname (`KC_HOSTNAME`), the API's redirect URI and whether cookies are
   `Secure`.
2. The Keycloak client: `scripts/keycloak-public-url.sh https://new.example.com` (or
   `restore.sh --public-url`). It logs in to Keycloak's `master` realm with
   `KEYCLOAK_ADMIN_USER` / `KEYCLOAK_ADMIN_PASSWORD` (after a restore: the source's admin
   login) and updates the two values with `kcadm.sh`.
3. Outside NeedWise: identity providers federated into Keycloak (company SSO) must allow the new
   broker URL (`<PUBLIC_URL>/auth/realms/needwise/broker/<alias>/endpoint`); the OAuth apps
   of Drive, Confluence and SharePoint sources need the new redirect URI
   (`<PUBLIC_URL>/api/v1/oauth/google/callback`, `.../oauth/atlassian/callback`,
   `.../oauth/microsoft/callback`); MCP clients and anyone with bookmarks need
   the new address. Personal access tokens keep working (they are not tied to the address).

## Measured: a restore drill

On 2026-11-12, on one laptop (Apple silicon, Docker Desktop with 8 CPUs and 16 GB), from the
development stack to a second stack, both Docker Compose. Two later fixes to `restore.sh` (it
stops only the services a release has; the status check reads the overall status) came from
the upgrade tests; the committed version restored a v0.1.0 backup with every check passing
([upgrade.md](upgrade.md#measured-v010-to-this-code)).

**Source:** the running development stack at `http://localhost:8480`, backed up `--online`
with `--no-compose` (Postgres reached over TCP) because it was in use and had to stay
untouched. Its data: 1 tenant (the Acme demo company), 20 users, 46 documents, 408 chunks,
35 tables with 2,668 rows, one Qdrant collection with 408 points, 49 objects (10 MB), an
Enterprise development license.

| Backup | Result |
|---|---|
| Time | 10 s (Postgres 7 s, Qdrant 2 s, objects under 1 s) |
| Size | 25.8 MB: `needwise.dump` 0.5 MB, `keycloak.dump` 0.3 MB, `openfga.dump` 6.8 MB (it also holds stores left by integration tests in that shared OpenFGA), Qdrant snapshot 8.9 MB, objects 9.3 MB compressed |
| Qdrant | the snapshot was deleted from the source's Qdrant afterwards (its snapshot list was empty) |

**Target:** compose project `needwise-ops` from a separate checkout, its own image tags
(`needwise-api:ops`, `needwise-web:ops`, through an override file), all host ports moved
(`HTTP_PORT=8580`, `PUBLIC_URL=http://localhost:8580`, Postgres 5532, Valkey 6479, Qdrant 6433,
OpenFGA 8501, API 8500, web 3500, Mailpit 8125), the same embedding server.

| Step | Time |
|---|---|
| Fresh stack (`docker compose up -d --wait`, images already built) | 38 s |
| `restore.sh` into it (Postgres 3 s, Qdrant 1 s, objects under 1 s, start 13 s) | 22 s |
| `keycloak-public-url.sh` | 4 s |
| Restore again over the restored stack with `--force --public-url` | 25 s |

| Check | Result |
|---|---|
| Row counts, all 35 tables | equal to the manifest (2,668 rows) |
| Alembic | 0019 in the backup and the code: no migration |
| Qdrant | 408 points; every point's id, payload, dense vector and BM25 vector identical to the source's (all 408 compared) |
| Objects | 49 of 49 with the same SHA-256; a document downloaded through the API matched the source object byte for byte |
| `nw authz sync` | 0 tuples written, 0 removed: OpenFGA already agreed with PostgreSQL |
| System status | ok (Postgres, Valkey, Qdrant, OpenFGA, Keycloak) |
| Sign-in on the new address, before `keycloak-public-url.sh` | refused by Keycloak: "Invalid parameter: redirect_uri" |
| Sign-in after it | 5 demo users and the guest signed in |
| Edition | Enterprise (the restored development license) |
| Search, 6 questions as 5 people and the guest | the same documents, sections and order as on the source, the same candidate counts; the guest got nothing on both. 5 of the 25 passages' fusion scores differed by at most 0.012 (see below) |
| Writing after the restore | an upload was indexed and found by search |
| Non-empty target without `--force` | refused, exit code 2, listing the tenant, the 409 points and the object files |
| `--pause` backup of the restored stack (compose mode) | 8 s, `worker` and `beat` stopped for 7 s and started again |
| `--offline` backup of the restored stack | 23 s, `api`, `web`, `worker` and `beat` stopped for 18 s |
| A v0.1.0 backup restored into this code (`--force`) | 56 s, migrations `0004` -> `0019` included ([upgrade.md](upgrade.md#measured-v010-to-this-code)) |

Why some scores differed: dense search returned the same points with the same scores on both
stacks; BM25 scores did not. Qdrant computes BM25's IDF from the segments it holds, and the
source's segments still contained points deleted earlier but not yet vacuumed (504 stored
vectors for 408 live points), while the restored collection holds only the live ones. Ranks
lower in the BM25 list moved, which changed some fused scores but not the top 5 of any
question.

**RTO and RPO from this drill.** With images built and a host ready, a full recovery took about
one minute (38 s for a fresh stack, 22 s for the restore), for this data size. Building the
images is extra (the upgrade test's builds are in [upgrade.md](upgrade.md)); restore time grows
with the dump and snapshot sizes, mostly in `pg_restore` and the snapshot upload. The RPO is
the backup interval: the backup itself took 10 seconds here.

How to run a drill like this: start a second stack from another checkout with
`COMPOSE_PROJECT_NAME=needwise-drill`, an override file that gives `migrate`, `api`, `worker`,
`beat`, `seed` and `web` their own `image:` tags, and every `*_PORT` variable and `PUBLIC_URL`
changed; restore into it with `scripts/restore.sh <backup> --public-url <its URL>`; compare
searches with `python3 scripts/lib/stack_checks.py search <url> <out.json>` on both; then
`docker compose -p needwise-drill down -v`.

## Kubernetes

Nothing here has been run on Kubernetes. The Helm chart's page lists what a backup of a
cluster install needs ([Kubernetes](../deploy/kubernetes.md#backups)). Pointers:

- **Postgres:** a managed database's snapshots and point-in-time recovery, or an operator such
  as CloudNativePG with WAL archiving, give a much better RPO than nightly `pg_dump`. The
  scripts' `PGHOST` mode dumps any Postgres they can reach with a superuser or `BYPASSRLS`
  role (above), which still gives a portable copy.
  Restore all three databases from the same moment, then run `nw authz sync`.
- **Qdrant:** the snapshot API is the same in a cluster; Qdrant can also be configured to write
  snapshots to S3-compatible storage (see Qdrant's snapshot documentation). One snapshot per
  collection, per node in a distributed setup.
- **Objects:** NeedWise stores objects on a filesystem (`OBJECT_STORAGE_PATH`; S3 storage is not
  built). Back up its volume with CSI volume snapshots or a file-level tool, taken after the
  database dump for the reason given under [Consistency](#consistency).
- **Velero** (or similar) copies volumes and resources together, but the copy is only as
  consistent as the application was quiet: scale `worker` and `beat` to zero first, as
  `--pause` does.
- **Secrets** belong in the cluster's secret store, backed up separately from the data.
