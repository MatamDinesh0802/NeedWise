# Kubernetes (Helm)

WorkMind ships a Helm chart at [`deploy/helm/workmind`](../../deploy/helm/workmind). It installs
the same processes as `compose.yaml`: the API, the Celery worker, Celery beat and the web app,
the database migrations as a Helm hook, and optionally Postgres, Valkey, Qdrant, OpenFGA,
Keycloak and an embedding server. This page covers what you need, how to install and upgrade,
the settings that matter, secrets, external services, backups and what the chart does not do.

**What has been checked, and how.** The chart was installed, tested, upgraded and uninstalled
on local [kind](https://kind.sigs.k8s.io) clusters: Kubernetes 1.37.0 with Helm 3.19.0 and
1.34.11 with Helm 4.0.0, everything bundled except the embedding server (hash embeddings), no
model key, behind ingress-nginx, in a namespace that enforces the "restricted" Pod Security
Standard, with NetworkPolicies on. The migrate and seed hooks, `helm test`, the API end-to-end
tests and the browser suite passed; the four chart problems the test found are fixed.
[Tested on a cluster](#tested-on-a-cluster) has the details and what was not tested: TLS, a
cloud load balancer or ingress, managed databases and the embedding server among them.
`make helm-kind-test` repeats it. Without a cluster, `deploy/helm/validate.sh` (CI:
`.github/workflows/helm.yml`, with Helm 3.19 and 4.0) runs `helm lint --strict`, renders the
chart with the default values, both examples and two CI values files (191 resources),
validates them with `kubeconform -strict` against the Kubernetes 1.33 schemas, checks every
pod spec against the chart's security rules, and checks that eleven kinds of invalid values
are refused. Before the cluster test, every image was also run in Docker with the user, group,
read-only root filesystem and dropped capabilities the chart gives it, the embedding server
included.

## Contents

- [What it installs](#what-it-installs)
- [Prerequisites](#prerequisites)
- [Install](#install)
- [Important values](#important-values)
- [Routing: Ingress or Gateway API](#routing-ingress-or-gateway-api)
- [Secrets](#secrets)
- [External services](#external-services)
- [Upgrades and the migrate hook](#upgrades-and-the-migrate-hook)
- [Backups](#backups)
- [Uninstall](#uninstall)
- [Security settings](#security-settings)
- [Tested on a cluster](#tested-on-a-cluster)
- [What is not covered](#what-is-not-covered)

## What it installs

| Component | Kubernetes objects | Replicas | Compose equivalent |
|---|---|---|---|
| `api` | Deployment, Service, PodDisruptionBudget, optional HPA | 1 (`api.replicas`) | `api` |
| `worker` | Deployment, PodDisruptionBudget, optional HPA | 1 | `worker` |
| `beat` | Deployment (Recreate), PVC for the schedule | always 1 | `beat` and its `beat_schedule` volume |
| `web` | Deployment, Service, PodDisruptionBudget, optional HPA | 1 | `web` |
| `migrate` | Job as a Helm hook | | `migrate` |
| `authz-sync` | Job as a Helm hook (`migrate.authzSync`) | | `docker compose run --rm migrate wm authz sync`, by hand after an upgrade |
| `seed` | Job as a Helm hook, off by default | | `seed` (profile `tools`) |
| object storage | PersistentVolumeClaim at `/data/objects` | | `./.data/objects` |
| `postgresql` | StatefulSet, Services, ConfigMap with the init script | 1 | `postgres` |
| `valkey` | StatefulSet, Services | 1 | `valkey` |
| `qdrant` | StatefulSet, Services | 1 | `qdrant` |
| `openfga` | Deployment, Service, migrate Job | 1 (`openfga.replicas`) | `openfga`, `openfga-migrate` |
| `keycloak` | Deployment, Service, ConfigMap with the realm | 1 | `keycloak` |
| `embeddings` | Deployment, Service, optional PVC | 1 | `embeddings` (profile `embeddings`) |
| routing | Ingress, or a Gateway API HTTPRoute | | `caddy` |
| | NetworkPolicies, optional | | |

Mailpit is not included (it is a development mail catcher). The Ingress or HTTPRoute replaces
Caddy. The bundled services use the same images and versions as `compose.yaml`. OpenFGA and
Keycloak are Deployments, not StatefulSets: their state is in Postgres.

Resource names below start with `<fullname>`: the release name followed by `-workmind`, or the
release name alone when it already contains "workmind". Installed as `workmind`, the names are
`workmind-api`, `workmind-credentials`, `workmind-migrate` and so on.

## Prerequisites

- **Kubernetes 1.29 or later** (the chart is validated against the 1.33 schemas and uses
  gRPC probes, stable since 1.27; it was installed on 1.34 and 1.37, see
  [Tested on a cluster](#tested-on-a-cluster)) and **Helm 3.19 or later, or Helm 4**. Helm
  4.0.0's `helm template` assumes Kubernetes 1.20 unless told otherwise, which the chart's
  `kubeVersion` refuses: pass `--kube-version` (e.g. `--kube-version 1.33.0`). `helm install`
  and `helm upgrade` use the cluster's version.
- **Images.** No WorkMind images are published yet. Build both and push them to a registry
  your cluster can pull from:

  ```bash
  docker build --build-context ee=ee -t registry.example.com/workmind/workmind-api:0.0.1 apps/api
  docker build -t registry.example.com/workmind/workmind-web:0.0.1 apps/web
  docker push registry.example.com/workmind/workmind-api:0.0.1
  docker push registry.example.com/workmind/workmind-web:0.0.1
  ```

  The API image contains the Enterprise package, which stays inactive without a license
  ([enterprise.md](../enterprise.md)). For several architectures, use `docker buildx build
  --platform linux/amd64,linux/arm64 --push`.
- **Storage.** A default StorageClass for the PersistentVolumeClaims. The object storage
  volume is ReadWriteOnce by default: see [Object storage](#object-storage) before running
  more than one node.
- **Routing.** An ingress controller, or a Gateway API implementation with a Gateway. A DNS
  name for `config.publicUrl` and, outside a test, a TLS certificate (cert-manager works
  through an Ingress annotation).
- **Outbound network access** from the pods: the model provider's API, Hugging Face (the
  embedding model on first start, about 330 MB, and the speech-to-text model on the first
  recording), and the sources your connectors read.
- **Capacity.** With the default values, the resource requests add up to 2.3 CPU and about
  5.3 GiB of memory, and the memory limits to about 20 GiB (sums of `values.yaml`, not
  measurements). The volumes request 20 GiB (Postgres) + 20 GiB (Qdrant) + 20 GiB (objects)
  + 2 GiB (Valkey) + 128 MiB (beat).

## Install

Everything bundled, with an Ingress:

```bash
helm install workmind deploy/helm/workmind --namespace workmind --create-namespace \
  --set config.publicUrl=https://workmind.example.com \
  --set images.api.repository=registry.example.com/workmind/workmind-api \
  --set images.web.repository=registry.example.com/workmind/workmind-web \
  --set ingress.className=<your ingress class> \
  --set ingress.tls.enabled=true \
  --set credentials.anthropicApiKey.existingSecret=model-keys \
  --set credentials.anthropicApiKey.key=anthropic \
  --wait --timeout 20m

helm test workmind --namespace workmind
```

What happens, in order:

1. Helm creates the Secret with generated credentials, the ConfigMap, the volumes, the bundled
   services and the WorkMind Deployments. OpenFGA's migrate Job runs next to OpenFGA, which
   reports "not serving" until its schema exists.
2. With `--wait`, Helm waits until everything is ready. Keycloak's first start (it builds
   itself for Postgres, then imports the realm) and the embedding server's model download
   take the longest; the startup probes allow 10 and 15 minutes.
3. The `migrate` hook Job runs `alembic upgrade head`. With the bundled Postgres this is a
   post-install hook: a pre-install hook would run before the database exists. Until it
   finishes, the API and the workers log database errors (the API's `/healthz` stays up).
4. The `authz-sync` hook Job runs `wm authz sync` (below); on a first install there is no
   tenant yet, so it does nothing.
5. If `seed.enabled`, the seed Job loads the demo company (below).

`helm test` runs a pod that calls the API's `/readyz` (Postgres, Valkey, Qdrant, OpenFGA and
Keycloak) and the web app's home page.

Without a model key, search works and answers fail until a key is set; organizations can also
add their own provider keys in Admin > Models ([models.md](../models.md)).

### Demo data

`seed.enabled: true` runs `python -m workmind.seed` after install and after every upgrade (it is
safe to re-run): the Acme Robotics company, its logins (password `credentials.demoPassword`,
default `workmind-demo`), 47 documents through the ingestion pipeline, analytics data and the
authorization tuples. It refuses `config.appEnv: production`; use `demo`. The repository's
`seed/` directory (about 9 MB) is not in the API image, so the Job fetches it in an init
container, from `seed.data.source`:

- `image`: an image that contains it, e.g. built from the repository root with
  `printf 'FROM busybox:1.37\nCOPY seed /seed\n' | docker build -t <registry>/workmind-seed:0.0.1 -f - .`
- `archive`: a `.tar.gz` of the repository at `seed.data.archiveUrl` (its top directory is
  stripped);
- `existingClaim`: a PersistentVolumeClaim with the directory's contents at its root.

Seeding with documents takes several minutes: give `helm install` a `--timeout` of 20 minutes
or more. `seed.args: [--skip-corpus]` skips the documents.

## Important values

Every key is in [`values.yaml`](../../deploy/helm/workmind/values.yaml), with comments, and
[`values.schema.json`](../../deploy/helm/workmind/values.schema.json) rejects unknown keys and
wrong types. The ones you will most likely set:

| Key | Default | What it does |
|---|---|---|
| `config.publicUrl` | `http://workmind.localhost` | The URL people use, without a path. Login redirects, Keycloak's hostname, the cookie (`Secure` and `__Host-` with https) and the Ingress host come from it |
| `config.appEnv` | `production` | `production` refuses development secrets and hides the API docs. `demo` for a demo with seed data |
| `config.llm.*` | Anthropic, `claude-sonnet-5-5` | Provider, model, per-role models, an OpenAI-compatible `baseUrl` ([models.md](../models.md)) |
| `config.embedding.*` | the bundled server | `baseUrl` of any OpenAI-compatible embedding server; `model` and `dimensions` must match it |
| `config.env` | `{}` | Any other setting of `apps/api/src/workmind/core/config.py`, as environment variables |
| `images.api`, `images.web` | `workmind-api`, `workmind-web`, tag = appVersion | Where your images are. A `digest` wins over the tag |
| `credentials.*` | generated or empty | See [Secrets](#secrets) |
| `postgresql.enabled`, `valkey.enabled`, `qdrant.enabled`, `openfga.enabled`, `keycloak.enabled`, `embeddings.enabled` | `true` | Bundle the service, or use yours ([External services](#external-services)) |
| `database.*` | `workmind`, `workmind_app`, `workmind_owner` | The WorkMind database: host (external), names, `urlParams` such as `ssl=require` |
| `objectStorage.*` | 20 GiB, ReadWriteOnce, `colocatePods: true` | The shared files volume ([Object storage](#object-storage)) |
| `api.replicas`, `web.replicas`, `worker.replicas` | 1 | Fixed replica counts; `*.autoscaling.enabled` adds an HPA on CPU (and memory if set) |
| `worker.queues`, `worker.concurrency` | `[ingest, default]`, 2 | The Celery command line |
| `*.resources` | see values.yaml | Requests and limits of every container. The API runs the analytics query workers, split across its `api.workers` processes (at least one each): raise its memory with `ANALYTICS_WORKERS`, `ANALYTICS_WORKER_MEMORY_MB` and `api.workers` |
| `*.startupProbe`, `*.livenessProbe`, `*.readinessProbe` | the compose healthchecks | Any probe object; `{}` removes one |
| `ingress.*`, `httpRoute.*` | Ingress on | [Routing](#routing-ingress-or-gateway-api) |
| `networkPolicy.enabled`, `networkPolicy.publicFrom` | on, any source | [Security settings](#security-settings) |
| `seed.*` | off | [Demo data](#demo-data) |
| `global.imageRegistry`, `global.imagePullSecrets`, `global.storageClass` | | A registry mirror, pull secrets and a storage class for everything |

**Probes.** As in compose, the API's probes use `/healthz` (the process is up) and the web
app's use `/`. The API also has `/readyz`, which checks Postgres, Valkey, Qdrant, OpenFGA and
Keycloak; it is not the default readiness path because one slow dependency would then take
every API pod out of the Service at once. The worker and beat have no probes, as in compose;
`values.yaml` shows a working worker liveness probe (`celery inspect ping`, about 2 s per call,
measured in Docker) and `examples/production.yaml` turns it on.

### Object storage

Uploaded files, meeting recordings and analytics datasets live on one volume mounted at
`/data/objects` in the API, the worker and the seed Job. The default access mode is
ReadWriteOnce, which every storage class supports but which a volume can only be attached to
on one node. With `objectStorage.colocatePods: true` (the default) the chart adds a required pod
affinity so that every pod mounting the volume lands on the same node; the first one may go
anywhere. That works for one node's worth of API and worker pods. To spread them over several
nodes, use a ReadWriteMany storage class (NFS, CephFS, Amazon EFS, Azure Files...), set
`objectStorage.accessModes: [ReadWriteMany]` and `colocatePods: false`. The analytics datasets
are DuckDB files on this volume; prefer a filesystem with working file locks. The claim is kept
when the release is uninstalled (`objectStorage.keepOnUninstall`).

## Routing: Ingress or Gateway API

Both have the routes of `deploy/caddy/Caddyfile`:

| Path | Goes to |
|---|---|
| `/api/auth/backchannel-logout` (exact) | the web app, which answers 404. Keycloak calls this endpoint on the cluster network; Caddy answers 404 to the outside, and so does this route |
| `/api`, `/mcp` (prefix) | the API |
| `/auth` (prefix) | the bundled Keycloak (not routed with an external Keycloak) |
| everything else | the web app |

`ingress.host` defaults to the host of `config.publicUrl`; `ingress.tls.enabled` adds a TLS
section with `ingress.tls.secretName` (default `<fullname>-tls`). Set your controller's
options through `ingress.annotations`. Two matter:

- **Request body size.** Meeting recordings are up to 500 MB (`MAX_MEETING_BYTES`) and
  documents up to 50 MB. Many controllers limit bodies to 1 MB by default.
- **Streaming.** Answers stream over Server-Sent Events. The API sends
  `X-Accel-Buffering: no`, which NGINX-based controllers honor; other proxies may need
  buffering turned off and a read timeout of a few minutes for long agent runs.

Caddy also adds `X-Content-Type-Options: nosniff`, `Referrer-Policy:
strict-origin-when-cross-origin` and `X-Frame-Options: SAMEORIGIN`, and removes `Server`. With
an Ingress, set those in your controller (the web app sends its own Content-Security-Policy).
The HTTPRoute (`httpRoute.enabled: true`, `ingress.enabled: false`, `httpRoute.parentRefs` set
to your Gateway) adds them with a `ResponseHeaderModifier` filter, which is "extended" support
in the Gateway API: check that your implementation has it, or set `httpRoute.securityHeaders:
false`.

## Secrets

Every credential has an entry under `credentials` with `value`, `existingSecret` and `key`.
For each one the chart uses, in this order:

1. its own `existingSecret` (a Secret you manage) and `key`;
2. its `value`, stored in the chart's Secret `<fullname>-credentials`;
3. `credentials.existingSecret`, one Secret you manage for all of them, under each entry's `key`;
4. a value it generates on the first install and reuses afterwards (only where the table says so).

| Entry | Key | Environment variable | Generated |
|---|---|---|---|
| `databaseAppPassword` | `database-app-password` | in `DATABASE_URL` (runtime role, no BYPASSRLS) | yes |
| `databaseOwnerPassword` | `database-owner-password` | in `MIGRATIONS_DATABASE_URL` (migrate and seed Jobs only) | yes |
| `postgresPassword` | `postgres-password` | `POSTGRES_PASSWORD` of the bundled Postgres | with `postgresql.enabled` |
| `keycloakDbPassword` | `keycloak-db-password` | `KC_DB_PASSWORD` | with `keycloak.enabled` |
| `openfgaDbPassword` | `openfga-db-password` | `OPENFGA_DATASTORE_PASSWORD` | with `openfga.enabled` |
| `valkeyPassword` | `valkey-password` | in `VALKEY_URL` and `CELERY_BROKER_URL` | with `valkey.enabled` and `valkey.auth.enabled` |
| `openfgaApiKey` | `openfga-api-key` | `OPENFGA_API_KEY` (and OpenFGA's pre-shared key) | with `openfga.enabled` |
| `oidcClientSecret` | `oidc-client-secret` | `OIDC_CLIENT_SECRET` (and the imported realm's client) | with `keycloak.enabled` |
| `secretsKey` | `secrets-key` | `SECRETS_KEY` (connector credentials) | yes |
| `secretsEncryptionKey` | `secrets-encryption-key` | `SECRETS_ENCRYPTION_KEY` (tenants' provider keys) | yes |
| `secretsEncryptionPreviousKeys` | `secrets-encryption-previous-keys` | `SECRETS_ENCRYPTION_PREVIOUS_KEYS` | no |
| `keycloakAdminPassword` | `keycloak-admin-password` | Keycloak's bootstrap admin; `KEYCLOAK_ADMIN_PASSWORD` for the seed | with `keycloak.enabled` |
| `demoPassword` | `demo-password` | `DEMO_PASSWORD` (seed) | no |
| `anthropicApiKey`, `llmApiKey`, `localLlmApiKey`, `embeddingApiKey`, `transcriptionApiKey` | `anthropic-api-key`, ... | `ANTHROPIC_API_KEY`, `LLM_API_KEY`, ... | no |
| `smtpPassword`, `googleClientSecret`, `slackClientSecret` | `smtp-password`, `google-client-secret`, `slack-client-secret` | `SMTP_PASSWORD`, `GOOGLE_CLIENT_SECRET`, `SLACK_CLIENT_SECRET` | no |
| `licenseKey` | `license-key` | `LICENSE_KEY` (Enterprise) | no |
| `mcpServers` | `mcp-servers` | `MCP_SERVERS` (JSON with the servers' tokens) | no |

The chart refuses to render when a credential it needs has no source, e.g. `oidcClientSecret`
with an external Keycloak.

Things to know:

- **Keep the chart's Secret.** It is annotated `helm.sh/resource-policy: keep`, so `helm
  uninstall` leaves it. Losing `SECRETS_ENCRYPTION_KEY` or `SECRETS_KEY` makes the stored
  provider keys and connector credentials unreadable, and the bundled Postgres keeps the
  passwords it was initialized with. Back the Secret up. A reinstall with the same release
  name and namespace adopts and reuses it ([Uninstall](#uninstall)).
- **Your own Secret needs a name of its own.** `credentials.existingSecret`, and each
  credential's `existingSecret`, must not be `<fullname>-credentials`, the Secret the chart
  creates: the chart refuses it (it would otherwise create that Secret itself, with generated
  values for every key).
- **Generated values need a cluster lookup.** `helm install` and `helm upgrade` read the
  existing Secret (Helm's `lookup`). Tools that only run `helm template`, such as Argo CD,
  would generate new values on every sync: give every credential a value or an existing
  Secret there.
- **Passwords go into URLs.** The database and Valkey passwords are inserted into connection
  URLs as they are, and the Valkey password into a config line in quotes. Use letters and
  digits (the generated ones are 32 of them), or percent-encode for the database.
- **Changing a database password in the values does not change the role.** The bundled
  Postgres runs its init script only on an empty volume. Run `ALTER ROLE ... PASSWORD` first,
  then update the Secret.
- **Rotating `SECRETS_ENCRYPTION_KEY`:** put the old key into
  `secretsEncryptionPreviousKeys`, the new one into `secretsEncryptionKey`, upgrade, then run
  `wm secrets rotate` in an API pod ([models.md](../models.md)).
- **Enterprise.** `credentials.licenseKey` sets `LICENSE_KEY`. No production signing key is
  published yet, so only development licenses exist, and those are trusted only with
  `config.appEnv: dev` and `config.licenseDevPublicKey` ([enterprise.md](../enterprise.md)).

## External services

Set `<service>.enabled: false` and point the chart at your service.
[`examples/external-services.yaml`](../../deploy/helm/workmind/examples/external-services.yaml)
does it for all of them.

**Postgres.** PostgreSQL 18 or later is required: every migration from `0002` on uses
`uuidv7()`, which is new in 18, so on 16 or 17 the migrate hook fails at `0002` (`function
uuidv7() does not exist`) and `helm install` with it. Compose and CI use 18.6. Set `database.host`,
`database.port` and, for TLS, `database.urlParams: ssl=require`. Create the roles and the
database the way `deploy/postgres/init/01-databases.sh` does, as a user that is a member of
`workmind_owner` (on a managed service, `GRANT workmind_owner TO <admin user>` first: Postgres
requires the membership to create a database owned by that role and to set its default
privileges):

```sql
CREATE ROLE workmind_owner LOGIN PASSWORD '...';
CREATE ROLE workmind_app LOGIN PASSWORD '...' NOBYPASSRLS;
CREATE DATABASE workmind OWNER workmind_owner;
-- connected to the workmind database:
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO workmind_app;
ALTER DEFAULT PRIVILEGES FOR ROLE workmind_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO workmind_app;
ALTER DEFAULT PRIVILEGES FOR ROLE workmind_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO workmind_app;
```

The runtime role must not have `BYPASSRLS` and must not own the tables: tenant isolation rests
on forced row-level security ([ARCHITECTURE 8](../ARCHITECTURE.md#8-multi-tenancy-and-data-isolation),
[ADR 0005](../adr/0005-postgres-roles-and-rls.md)). With the bundled Keycloak or OpenFGA and an
external Postgres, also create their roles and databases (`keycloak`, `openfga`, as in the
script); they connect to `database.host` unless `keycloak.database.host` or
`openfga.database.host` is set.

**Valkey** (or Redis). `valkey.external.host`, `port`, `tls` (`rediss://`). WorkMind uses
database 0 (`VALKEY_URL`: login sessions and short-lived application data such as answer
event streams) and database 1 (`CELERY_BROKER_URL`: the task queue). With
`valkey.auth.enabled` (the default) the chart requires `credentials.valkeyPassword`; set
`valkey.auth.enabled: false` for a server without a password.

**Qdrant.** `qdrant.external.url`, and `credentials.qdrantApiKey` with the key the server was
started with (`QDRANT__SERVICE__API_KEY`); the API, worker and seed send it as `api-key`. The
index holds every tenant's chunk text, so do not leave the server open on the network.

**OpenFGA.** `openfga.external.url` (the HTTP API) and `credentials.openfgaApiKey`, one of the
server's pre-shared keys. WorkMind creates one store per tenant and writes the model itself.

**Keycloak.** `keycloak.external.url` is the base URL the API calls, including Keycloak's
relative path if it has one (the bundled one uses `/auth`). Import
[`deploy/keycloak/realm-workmind.json`](../../deploy/keycloak/realm-workmind.json) with
`WORKMIND_OIDC_CLIENT_SECRET` and `WORKMIND_PUBLIC_URL` set (Keycloak replaces those
placeholders), and change the client's back-channel logout URL from `http://api:8000/...` to
`http://<fullname>-api.<namespace>.svc:8000/api/auth/backchannel-logout`. Put the same
client secret into `credentials.oidcClientSecret`. The `/auth` route is not created; the login
pages are served by your Keycloak's own host.

**Embeddings.** `embeddings.enabled: false` and `config.embedding.baseUrl` set to any
OpenAI-compatible `/v1/embeddings` server, with `model` and `dimensions` matching it. Changing
the embedding model means re-indexing ([ARCHITECTURE 8](../ARCHITECTURE.md#8-multi-tenancy-and-data-isolation)).
`config.embedding.provider: hash` needs no server, for tests only.

## Upgrades and the migrate hook

```bash
helm upgrade workmind deploy/helm/workmind -n workmind -f my-values.yaml --wait --timeout 20m
```

- **Migrations run before the new pods.** The `migrate` Job is a `pre-upgrade` hook (and
  `pre-install` with an external database): `alembic upgrade head` with the owner role, after
  an init container has waited for Postgres. Only when it succeeds does Helm roll the
  Deployments. While they roll, pods of the old version run against the new schema;
  migrations only go forward ([ROADMAP §6](../ROADMAP.md#6-release-process)) and breaking
  changes are called out in the release notes.
- **When it fails**, the upgrade stops and the release is marked failed; the running pods are
  not touched. Its logs: `kubectl logs -n workmind job/workmind-migrate` (the Job stays until
  the next run).
- **Hook resources.** On a first install with an external database, the release's Secret and
  ServiceAccount do not exist yet when pre-install hooks run, so the Job brings its own hook
  ServiceAccount and, when the chart holds the owner password, a hook Secret with only that
  password. Both are deleted once the hooks succeed.
- **Authorization stores.** Once the new pods are ready, the `authz-sync` hook Job runs `wm
  authz sync` with the owner role: every active tenant's OpenFGA store gets the current
  authorization model, and its tuples are recomputed from Postgres. The Compose procedure runs
  the same command by hand after every upgrade ([upgrades](../operations/upgrade.md)). It is
  safe to repeat; `migrate.authzSync: false` turns it off. Its logs: `kubectl logs -n workmind
  job/workmind-authz-sync`.
- **`helm rollback` does not downgrade the schema.**
- **OpenFGA** migrates in a Job named after the release revision, so every install and upgrade
  runs it once; OpenFGA reports "not serving" until it is done.
- **Keycloak imports the realm only once**, when it does not exist. Later changes, such as a new
  `config.publicUrl`, go through Keycloak's admin console (the client's redirect URIs and
  post-logout URIs).
- **Bundled Postgres major versions** are not upgraded by the chart: a new major version needs
  `pg_upgrade` or a dump and restore.
- **What restarts.** A pod is replaced when its own values change, or when the data of the
  chart's ConfigMap or Secret changes (checksum annotations); a new chart or app version alone
  restarts no bundled service. On kind, an upgrade that only added a label replaced no running
  pod, and one that changed a setting (`config.env`) replaced only the API, worker and beat pods.
  Credentials in Secrets you manage are not in the checksums: after changing one, restart the
  pods that read it (`kubectl rollout restart`).
- **Keycloak runs one replica.** While its pod is replaced (when its credentials or the realm
  file change), a sign-in can fail: on kind, one request in about 70 timed out (5 s) during
  that replacement.

## Uninstall

```bash
helm uninstall workmind -n workmind
```

It deletes the Deployments, StatefulSets, Services, ConfigMaps, the Ingress, the
NetworkPolicies, the PodDisruptionBudgets and the beat schedule's claim. What stays (checked
on kind):

- the chart's Secret `<fullname>-credentials` and the object storage claim `<fullname>-objects`,
  both annotated `helm.sh/resource-policy: keep` (`objectStorage.keepOnUninstall: false` lets
  the claim go);
- the bundled services' claims `data-<fullname>-postgresql-0`, `data-<fullname>-qdrant-0` and
  `data-<fullname>-valkey-0`: Kubernetes keeps a StatefulSet's claims when it is deleted;
- the last `migrate`, `authz-sync` and `seed` hook Jobs and the `helm test` pod, with their
  logs: Helm does not delete hook resources on uninstall (the next install replaces them).

Installed again with the same release name and namespace, the chart adopts the kept Secret
and claims. On kind, the new release found its database, files and index: the seed reported
its 47 documents unchanged, and `helm test` and the API end-to-end tests passed as before. To
remove everything, delete the namespace, or those objects by hand; a deleted claim's volume
then follows its storage class's reclaim policy.

## Backups

The scripts in [backup and restore](../operations/backup-restore.md) are for Compose and have
not been run on Kubernetes; that page explains the consistency rules that apply here too. A
backup of a Kubernetes install needs all of these, taken together:

- **Postgres:** the `workmind`, `keycloak` and `openfga` databases (`pg_dump`, or your managed
  service's snapshots). With the bundled server: `kubectl exec` into
  `<fullname>-postgresql-0` and run `pg_dump`, or snapshot its volume.
- **The object storage volume** (`<fullname>-objects`): uploaded files, recordings and
  analytics datasets. Postgres refers to these files.
- **Qdrant:** its snapshots API writes to `/qdrant/snapshots`, in the bundled pod an emptyDir
  limited to `qdrant.snapshotsSizeLimit` (5Gi): copy the files out. A snapshot larger than
  that gets the pod evicted, which takes search down, so raise the limit with the index (a
  snapshot is not proportional to the points: 50.9 MB for 421 points on the development stack,
  8.9 MB for 408 in the backup drill). Every call but the health checks needs the `api-key`
  header with the key in the credentials Secret (`qdrant-api-key`). The index can also be
  rebuilt from the documents by re-indexing.
- **The credentials Secret** (`<fullname>-credentials`) or your own Secret: without
  `SECRETS_ENCRYPTION_KEY` and `SECRETS_KEY`, a restored database has unreadable provider keys
  and connector credentials.
- Valkey holds sessions, queued tasks and caches. Losing it signs people out and drops queued
  tasks; it is usually not backed up.

After restoring, OpenFGA must be brought in line with Postgres (`wm authz sync`). The API pods
have no owner-role database URL, so run it through the hook: `helm upgrade` with the same
values runs the `authz-sync` Job again (or apply that Job's manifest from `helm get manifest`).

## Security settings

- Every pod: `runAsNonRoot` with a fixed user (WorkMind 10001, web 1001, Postgres 999, Valkey
  999, Qdrant 1000, Keycloak 1000, OpenFGA and the embedding server 65532), the
  `RuntimeDefault` seccomp profile, no service account token
  (`automountServiceAccountToken: false`).
- Every container: `allowPrivilegeEscalation: false`, all capabilities dropped, CPU and memory
  requests and limits, and a read-only root filesystem with emptyDir volumes where the
  process writes (`/tmp`, Next.js's cache, Postgres's socket directory, Qdrant's marker
  file). The exception is Keycloak: the stock image builds itself on `start` and writes into
  `/opt/keycloak/lib`, which fails on a read-only filesystem (tried in Docker).
- These are the rules of the "restricted" Pod Security Standard; `check-manifests.py` checks
  them on every rendered pod in CI. On kind (Kubernetes 1.34 and 1.37), a namespace labelled
  `pod-security.kubernetes.io/enforce: restricted` admitted every pod of the chart, the hook
  Jobs and the `helm test` pod included, with no warning.
- Credentials are only in Secrets and reach containers as `secretKeyRef` variables; the
  ConfigMap holds no credential (checked in CI). Valkey reads its password from a file on a
  memory volume, not from its command line.
- `networkPolicy.enabled` (on by default since chart 0.1.5) adds ingress NetworkPolicies: the
  web app, the API and Keycloak accept `networkPolicy.publicFrom` (set it to your ingress
  controller's or gateway's namespace; empty means any source) plus the WorkMind pods that
  call them; each bundled service accepts only the WorkMind pods that use it; the worker and
  beat accept nothing. Egress is not restricted. They take effect only where the cluster's
  network plugin enforces NetworkPolicies. Before 0.1.5 they were off by default, so any pod
  in the cluster could read and write the bundled Qdrant, which had no API key.
- The bundled Qdrant requires an API key (generated into the credentials Secret), so a pod
  that can reach it still needs the key.
- The API trusts `X-Forwarded-*` headers from any address (`api.forwardedAllowIps: "*"`, as
  in compose). Only the Service reaches the pod; with `networkPolicy.publicFrom` set, only the
  listed sources. Left empty, any pod can call the API directly and choose the client address
  that the per-address sign-in limit counts: set it in production.
- Permission checks, tenant isolation and the database roles are the application's, and do
  not change with the deployment: the runtime role has no `BYPASSRLS`, the owner role is used
  only by the migrate and seed Jobs.

## Tested on a cluster

On 2026-11-13 the chart was installed on local kind clusters, on one laptop (Apple M5, 24 GB,
macOS; Docker Desktop 29.4.3 with 8 CPUs and 15.6 GiB). Run 1, by hand, found the first two
problems listed below (the other two came up while fixing and documenting them), and the
fixes were checked on its cluster; the later runs used `deploy/helm/kind-test.sh` and the
fixed chart. These are local clusters, not a cloud: read
the results as "the chart and the app work on Kubernetes", not as a production qualification.

| Run | Kubernetes (`kindest/node`) | Helm, kubectl | How | Chart | `RETRIEVAL_MIN_SIMILARITY` |
|---|---|---|---|---|---|
| 1 | 1.37.0 | 3.19.0, 1.37.2 | step by step, by hand | 0.1.0, then each fix (to 0.1.3) | `0`, as CI's compose stack |
| 2 | 1.34.11 | 4.0.0, 1.34.13 | `deploy/helm/kind-test.sh` | 0.1.3 | the default (0.25) |
| 3 | 1.37.0 | 3.19.0, 1.37.2 | `make helm-kind-test` | 0.1.3 | the default |
| 4 | 1.37.0 | 3.19.0, 1.37.2 | `make helm-kind-test` | 0.1.4, with the `authz-sync` hook | the default |
| 5 | 1.34.11 | 4.0.0, 1.34.13 | `deploy/helm/kind-test.sh` | 0.1.4 | the default |
| 6 | 1.37.0 | 3.19.0, 1.37.2 | `deploy/helm/kind-test.sh` with `SKIP_BROWSER=1` | 0.1.5: the generated Qdrant API key | the default |

Run 6 (after the final review): the seed Job indexed 47 documents (364 chunks) through the
Qdrant API key, `helm test` and the API end-to-end tests (14 passed, 2 model tests skipped)
passed through the Ingress, and the upgrade to two API replicas and the uninstall went as in
the earlier runs. The browser suite was not run.

All: kind v0.33.0, one control-plane node and two workers (containerd 2.3.4, kindnet, the
local-path storage class), ingress-nginx controller v1.15.1 on the control plane, published
on `127.0.0.1:8680`. ingress-nginx was retired in March 2026 and v1.15.1 is its last release;
other controllers were not tried.

**Values** ([`ci/kind-values.yaml`](../../deploy/helm/workmind/ci/kind-values.yaml)): the API
and web images built from the checkout (the application code did not change between the runs)
and loaded into the nodes (`kind load docker-image`),
`config.publicUrl: http://localhost:8680`, `config.appEnv: demo`, Postgres, Valkey, Qdrant,
OpenFGA and Keycloak (production mode, `start`) bundled, the embedding server off and
`config.embedding.provider: hash`, no model key, no license (Community), the seed Job with
`seed.data.source: image` (the busybox image above), `ingress.className: nginx` with the body
size and read timeout annotations, and `networkPolicy.enabled: true` with `publicFrom` set to
the `ingress-nginx` namespace. The `workmind` namespace enforced the "restricted" Pod Security
Standard.

**What passed** (in every run unless a run is named):

| Step | Result |
|---|---|
| `helm install --wait` | Every pod admitted and ready, Keycloak included (46 s after the start in run 1, about 80 s in run 2). Then the `migrate` hook: 19 migrations (to `0019`) in about 6 s; in runs 4 and 5 the `authz-sync` hook (no tenant yet: nothing to do); then the `seed` hook: 47 documents (364 chunks), 4 analytics tables (527 rows), 164 authorization tuples, 9 logins, in under 10 s. The install returned after 61 to 103 s, with the images already on the nodes. Run 1: the hook ServiceAccount and Secret were deleted once the hooks had succeeded |
| `helm test` | `/readyz`: Postgres, Valkey, Qdrant, OpenFGA and Keycloak "ok"; the web app answered 200 |
| NetworkPolicies (run 1) | Enforced: from a pod in another namespace, and from an unlabelled pod in `workmind`, all seven Services refused connections; a pod labelled as the web component reached the API and nothing else |
| API end-to-end tests, through the Ingress and Keycloak | Runs 2 to 5: 14 passed (2 model tests skipped). Run 1: 13 passed, 1 failed: with `RETRIEVAL_MIN_SIMILARITY=0` the guest's one collection always matches, so her question goes to the model, which fails without a key, where the test expects "not found". With the default floor on the same install: 14 passed. The test assumes the real floor; it is not a chart problem |
| Browser suite (`make e2e-browser`) | 159 of 159 in each run (the 3 `@model` tests left out), in about a minute; run 1 also with two API replicas |
| `helm upgrade` to `api.replicas=2` | The `migrate` Job ran again before any Deployment changed (pre-upgrade), the `seed` Job after (0 documents changed), OpenFGA's migrate Job once more; in runs 4 and 5 the `authz-sync` Job too, which synced the seeded tenant (0 tuples written, 0 removed). Run 1: the generated credentials were kept (the Secret's data unchanged), and both API pods ran on the node of the object storage volume. A poller called `/api/v1/system/status`, `/` and `/auth/realms/workmind` every 0.25 s through the Ingress during the upgrade: run 1, 705 requests, all answered 200, but 3 status pages reported a dependency down (the second problem below); runs 2 to 5, 237, 228, 282 and 285 requests, all 200 with every dependency "ok" |
| `helm uninstall` | The Secret, the object storage claim, the three StatefulSet claims and the last hook Jobs were left, as listed in [Uninstall](#uninstall) |
| Install again (run 1) | Adopted the kept Secret and claims; data intact, `helm test` and the API tests as before |

**Problems it found, fixed in the chart:**

- The seed Job failed with `seed.data.source: image` and the default path: its init container
  mounted the copy's volume over the image's own `/seed`, so `helm install` failed in its
  post-install hook. Found by run 1's first install.
- Every chart upgrade restarted Valkey, Keycloak and OpenFGA: the checksum annotations hashed
  the rendered ConfigMap and Secret with their labels, which carry the chart version. During
  run 1's first upgrade the status page reported a dependency down for about 4 s. The
  checksums now cover the data only.
- Both examples (`external-services.yaml`, `production.yaml`) named their own Secret
  `workmind-credentials`, the chart's own Secret's name for a release called `workmind`: the
  chart then created that Secret itself with a generated value for every key (found while
  checking the previous fix). The chart now refuses that name.
- Upgrades never ran `wm authz sync`, which the upgrade procedure requires after every
  upgrade, and there was no easy way to run it: only the hook Jobs have the owner role's
  password. Found while comparing the chart's upgrade with the Compose procedure; the
  authorization model has not changed since v0.1.0, so no upgrade has needed it yet. The
  `authz-sync` hook Job now runs it after every install and upgrade (runs 4 and 5).

**Not tested on a cluster:** TLS (cert-manager, an `https` public URL and the `Secure`,
`__Host-` cookie), cloud load balancers and cloud ingress controllers, the Gateway API route
(`httpRoute`), managed or external databases and the other external services
(`examples/external-services.yaml`), `examples/production.yaml` (ReadWriteMany storage,
autoscaling, replicas spread over nodes), the bundled embedding server and real embeddings,
any model call, an Enterprise license, the `archive` and `existingClaim` seed sources, node
drains and failures (the PodDisruptionBudgets), backups and restores, an upgrade between two
WorkMind versions (both releases ran the same build, so the upgrade's migrate Job had nothing
new to apply; [operations/upgrade.md](../operations/upgrade.md) covers version upgrades with
Compose), and load.

**Repeat it:** `make helm-kind-test` (needs Docker, kind, kubectl, Helm, uv, pnpm and python3)
builds the images, creates the cluster `workmind-test` with
[`deploy/helm/kind/cluster.yaml`](../../deploy/helm/kind/cluster.yaml), and runs the install,
the checks, the upgrade and the uninstall above, then deletes the cluster and the images.
`KIND_NODE_IMAGE`, `HELM`, `KUBECTL`, `HTTP_PORT`, `SKIP_BROWSER=1` and `KEEP=1` change it;
it writes its kubeconfig to a temporary directory, never to `~/.kube/config`.

## What is not covered

- **No cluster install in CI.** CI lints, renders and schema-validates the chart. The kind
  test ([Tested on a cluster](#tested-on-a-cluster)) was run by hand on one laptop, and no
  cloud cluster has run the chart.
- **No published images or chart.** Build and push the images yourself; the chart is not in a
  Helm repository or OCI registry, and its version is not bumped by release-please yet.
- **The bundled services are single instances** without high availability, automated
  backups, monitoring or major-version upgrades. For production, prefer managed services or
  operators (e.g. CloudNativePG for Postgres, the Keycloak Operator) through the external
  settings. Keycloak runs one replica with a local cache.
- **No separate ingestion workers.** ARCHITECTURE 5.1 plans a `worker-ingest` process; the
  chart runs one worker Deployment for the `ingest` and `default` queues. Autoscaling is on
  CPU only: no queue-length autoscaling (KEDA).
- **No metrics scraping** (ServiceMonitor), dashboards or alerts. Tracing works through
  `config.env.OTEL_EXPORTER_OTLP_ENDPOINT`.
- **No egress NetworkPolicies.** The ingress policies (on by default) are not tested on a
  cloud provider's network plugin; on kind they were enforced (run 1).
- **Object storage is a volume**, not S3-compatible storage.
- **Not included:** Mailpit, Caddy (replaced by the Ingress or HTTPRoute), an Ollama or vLLM
  server for local models (point `config.llm.baseUrl` or `config.localLlm.baseUrl` at yours).
