# NeedWise Helm chart

Installs NeedWise on Kubernetes: the API, the Celery worker, Celery beat and the web app,
with the database migrations as a Helm hook. Postgres, Valkey, Qdrant, OpenFGA, Keycloak and
an embedding server can come bundled (simple single-replica workloads with the same images
as `compose.yaml`) or from services you run. The full guide is
[docs/deploy/kubernetes.md](../../../docs/deploy/kubernetes.md).

**Status.** The chart passes `helm lint --strict`, renders with the default values and the
examples, and validates against the Kubernetes 1.33 schemas (`deploy/helm/validate.sh`, run
in CI by `.github/workflows/helm.yml`). It was installed, tested, upgraded and uninstalled on
kind clusters with Kubernetes 1.37 (Helm 3.19) and 1.34 (Helm 4.0), everything bundled but the
embedding server, in a namespace that enforces the "restricted" Pod Security Standard
(`deploy/helm/kind-test.sh`; results and what was not tested in
[docs/deploy/kubernetes.md](../../../docs/deploy/kubernetes.md#tested-on-a-cluster)). It has
not run on a cloud cluster, and CI does not install it. No NeedWise images are published:
build them first (below).

## Install

```bash
# 1. Build and push the two images (no published images yet)
docker build --build-context ee=ee -t registry.example.com/needwise/needwise-api:1.0.0 apps/api
docker build -t registry.example.com/needwise/needwise-web:1.0.0 apps/web
docker push registry.example.com/needwise/needwise-api:1.0.0
docker push registry.example.com/needwise/needwise-web:1.0.0

# 2. Install, everything bundled
helm install needwise deploy/helm/needwise --namespace needwise --create-namespace \
  --set config.publicUrl=https://needwise.example.com \
  --set images.api.repository=registry.example.com/needwise/needwise-api \
  --set images.web.repository=registry.example.com/needwise/needwise-web \
  --set ingress.className=<your ingress class> --set ingress.tls.enabled=true \
  --wait --timeout 20m

# 3. Check every dependency from inside the cluster
helm test needwise --namespace needwise
```

The chart generates the database passwords, the OpenFGA key, the OIDC client secret and the
encryption keys on the first install, stores them in the Secret `<fullname>-credentials`
(`needwise-credentials` for a release named `needwise`; otherwise
`<release>-needwise-credentials`) and reuses them on upgrades. That Secret is kept when the release is uninstalled: back it up,
it holds the keys that decrypt stored provider keys and connector credentials.

Other setups:

| File | What it shows |
|---|---|
| [`values.yaml`](values.yaml) | Every setting, with comments; validated by [`values.schema.json`](values.schema.json) |
| [`examples/external-services.yaml`](examples/external-services.yaml) | Every dependency outside the chart, all credentials in one existing Secret |
| [`examples/production.yaml`](examples/production.yaml) | A managed Postgres, ReadWriteMany object storage, several replicas, autoscaling, NetworkPolicies, TLS through cert-manager |

## What it creates

| Component | Kind | Notes |
|---|---|---|
| api | Deployment, Service, PDB, optional HPA | Uvicorn on :8000. Probes on `/healthz`, as in compose |
| worker | Deployment, PDB, optional HPA | Celery, queues `ingest,default`; speech models in an emptyDir or a PVC |
| beat | Deployment (always 1 replica, Recreate), PVC | Celery beat; its schedule survives restarts |
| web | Deployment, Service, PDB, optional HPA | Next.js on :3000. Probes on `/`, as in compose |
| migrate | hook Job (+ hook ServiceAccount and Secret) | `alembic upgrade head` with the owner role |
| authz-sync | hook Job, after install and upgrade | `nw authz sync`: each tenant's OpenFGA store gets the current model and tuples (`migrate.authzSync`) |
| seed | hook Job, optional | The Acme Robotics demo; refuses `config.appEnv: production` |
| objects | PVC | `/data/objects`, shared by the API, the worker and the seed Job |
| postgresql | StatefulSet, Services, ConfigMap | `postgres:18.6` with `deploy/postgres/init` as a ConfigMap |
| valkey | StatefulSet, Services | `valkey/valkey:9.1.3-alpine`, password required |
| qdrant | StatefulSet, Services | `qdrant/qdrant:v1.20.0` |
| openfga | Deployment, Service, migrate Job | `openfga/openfga:v1.22.1`, pre-shared key |
| keycloak | Deployment, Service, ConfigMap | `quay.io/keycloak/keycloak:26.8.2-2` in production mode, realm imported |
| embeddings | Deployment, Service, optional PVC | `ghcr.io/ggml-org/llama.cpp:server-b12098` with EmbeddingGemma-300M |
| Ingress or HTTPRoute | | The routes of `deploy/caddy/Caddyfile` |
| NetworkPolicies | optional | Ingress rules per component |

OpenFGA and Keycloak are Deployments rather than StatefulSets: they keep their state in
Postgres, so a StatefulSet would add nothing but ordered rollouts.

## Files

- `files/postgres/01-databases.sh` and `files/keycloak/realm-needwise.json` are copies of the
  files in `deploy/postgres/init` and `deploy/keycloak` (Helm can only read files inside the
  chart). `validate.sh` fails when they differ: edit the originals and copy them.
- `ci/` holds values used only by the checks (`validate.sh`, and `kind-test.sh` for
  `kind-values.yaml`); it is left out of a packaged chart.

## Validate

```bash
make helm-check   # = PYTHON="uv run --no-project --with pyyaml==6.0.2 python" deploy/helm/validate.sh
```

`HELM` and `KUBECONFORM` choose other binaries than the ones on `PATH`.

It needs Helm, [kubeconform](https://github.com/yannh/kubeconform) and network access to the
schema repositories. It lints, renders five sets of values, validates them against the
Kubernetes 1.33 schemas, checks every pod against the chart's rules (`../check-manifests.py`),
and checks that invalid values are refused.

On a throwaway kind cluster (needs Docker, kind, kubectl, uv, pnpm and python3 as well):

```bash
make helm-kind-test   # = deploy/helm/kind-test.sh: build, install, test, upgrade, uninstall, delete
```
