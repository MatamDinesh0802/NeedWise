#!/usr/bin/env bash
# Validates the WorkMind Helm chart without a cluster:
#   1. the chart's copies of deploy/postgres/init and deploy/keycloak match the originals,
#   2. helm lint --strict and helm template with the default values, the two examples and
#      the CI values,
#   3. kubeconform -strict against the Kubernetes schemas (KUBERNETES_VERSION, default 1.33.0),
#   4. the chart's own rules (check-manifests.py: non-root, read-only, limits, probes...),
#   5. values the chart must refuse.
# Needs helm, kubeconform and Python with PyYAML. Override the commands with HELM, KUBECONFORM
# and PYTHON, e.g.  PYTHON="uv run --no-project --with pyyaml python" deploy/helm/validate.sh
set -euo pipefail

cd "$(dirname "$0")/../.."
CHART=deploy/helm/workmind
HELM=${HELM:-helm}
KUBECONFORM=${KUBECONFORM:-kubeconform}
PYTHON=${PYTHON:-python3}
KUBERNETES_VERSION=${KUBERNETES_VERSION:-1.33.0}
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT

echo "== Shared files"
diff -u deploy/postgres/init/01-databases.sh "$CHART/files/postgres/01-databases.sh"
diff -u deploy/keycloak/realm-workmind.json "$CHART/files/keycloak/realm-workmind.json"
echo "files/ matches deploy/postgres/init and deploy/keycloak"

echo "== Lint and render"
render() { # name, values files...
  local name=$1
  shift
  local args=()
  for values in "$@"; do args+=(-f "$values"); done
  # ${args[@]+...}: an empty array under `set -u` (bash 3.2 on macOS). --kube-version: render
  # for the version we validate against (Helm 4.0.0's default for `template` is 1.20).
  $HELM lint --strict --kube-version "$KUBERNETES_VERSION" "$CHART" ${args[@]+"${args[@]}"}
  $HELM template workmind "$CHART" --namespace workmind --kube-version "$KUBERNETES_VERSION" \
    ${args[@]+"${args[@]}"} >"$OUT/$name.yaml"
  echo "$name: $(grep -c '^kind:' "$OUT/$name.yaml") resources"
}
render default
render external-services "$CHART/examples/external-services.yaml"
render production "$CHART/examples/production.yaml"
render gateway-demo "$CHART/ci/gateway-demo-values.yaml"

echo "== kubeconform (Kubernetes $KUBERNETES_VERSION)"
# HTTPRoute (Gateway API) is a CRD: its schema comes from the datreeio CRDs catalog.
$KUBECONFORM -strict -summary -kubernetes-version "$KUBERNETES_VERSION" \
  -schema-location default \
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
  "$OUT"/*.yaml

echo "== Chart rules"
$PYTHON deploy/helm/check-manifests.py "$OUT"/*.yaml

echo "== Values the chart refuses"
refuse() { # description, expected message, helm template arguments...
  local description=$1 expected=$2
  shift 2
  local output
  if output=$($HELM template workmind "$CHART" --kube-version "$KUBERNETES_VERSION" "$@" 2>&1); then
    echo "FAIL: accepted $description"
    return 1
  fi
  if ! grep -qF -- "$expected" <<<"$output"; then
    echo "FAIL: $description: expected '$expected' in:"
    echo "$output"
    return 1
  fi
  echo "refused: $description"
}
refuse "a misspelled key" "additional properties 'replica' not allowed" --set api.replica=2
refuse "a public URL with a path" "publicUrl" --set config.publicUrl=https://example.com/workmind
refuse "an external database without a host" "database.host is required" --set postgresql.enabled=false
refuse "an external Keycloak without its secret" "credentials.oidcClientSecret is required" \
  --set keycloak.enabled=false --set keycloak.external.url=https://sso.example.com
refuse "the demo seed in production" "refuses config.appEnv production" \
  --set seed.enabled=true --set seed.data.image.repository=seed
refuse "renamed roles on the bundled Postgres" "can only change with an external database" \
  --set database.appUser=app
refuse "no embedding server" "config.embedding.baseUrl is required" --set embeddings.enabled=false
refuse "an API without worker processes" "workers" --set api.workers=0
refuse "an external Valkey with auth and no password" "credentials.valkeyPassword is required" \
  --set valkey.enabled=false --set valkey.external.host=valkey.example
refuse "your Secret named like the chart's own" "the name of the Secret the chart creates itself" \
  --set credentials.existingSecret=workmind-credentials
refuse "one credential's Secret named like the chart's own" "credentials.licenseKey.existingSecret is" \
  --set credentials.licenseKey.existingSecret=workmind-credentials
echo "All chart checks passed."
