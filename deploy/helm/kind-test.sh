#!/usr/bin/env bash
# Installs the NeedWise Helm chart on a throwaway kind cluster and exercises it
# (docs/deploy/kubernetes.md, "Tested on a cluster"):
#   1. builds needwise-api:kind, needwise-web:kind and needwise-seed:kind from this checkout,
#      creates the kind cluster (deploy/helm/kind/cluster.yaml) and loads the images into it;
#   2. ingress-nginx (pinned manifest, checked by SHA-256) on the control plane, published on
#      127.0.0.1:$HTTP_PORT;
#   3. helm install with ci/kind-values.yaml, --wait, into a namespace that enforces the
#      "restricted" Pod Security Standard: the migrate, authz-sync and seed hook Jobs must
#      succeed;
#   4. helm test, the API end-to-end tests and the browser suite through the ingress;
#   5. helm upgrade to two API replicas while the app is polled through the ingress: the migrate
#      and authz-sync hooks must run again and every request must succeed;
#   6. helm uninstall, then what is left on purpose (the kept Secret and volumes).
# The cluster and the images are deleted at the end, unless KEEP=1.
#
# Needs Docker, kind, kubectl, helm (3.19 or later, or 4), uv, pnpm, python3 and curl. Override
# the commands with KIND, KUBECTL, HELM and PYTHON. Other settings: KIND_NODE_IMAGE, CLUSTER
# (needwise-test), HTTP_PORT (8680), SKIP_BROWSER=1. It never touches ~/.kube/config: the
# cluster's kubeconfig is in a temporary directory.
set -euo pipefail
cd "$(dirname "$0")/../.."

KIND=${KIND:-kind}
KUBECTL=${KUBECTL:-kubectl}
HELM=${HELM:-helm}
PYTHON=${PYTHON:-python3}
CLUSTER=${CLUSTER:-needwise-test}
HTTP_PORT=${HTTP_PORT:-8680}
# The default node image of kind v0.33.0 (its release notes list the digests of the others).
KIND_NODE_IMAGE=${KIND_NODE_IMAGE:-kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5}
INGRESS_NGINX_URL=https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/kind/deploy.yaml
INGRESS_NGINX_SHA256=2a3ae008c8786431115502644e77ab398fdebfb721a5d1195ed3089cde3299df
CHART=deploy/helm/needwise
NS=needwise
RELEASE=needwise
BASE_URL=http://localhost:$HTTP_PORT
WORK=$(mktemp -d)
export KUBECONFIG=$WORK/kubeconfig

step() { printf '\n== %s (%s UTC)\n' "$*" "$(date -u +%H:%M:%S)"; }
fail() {
  echo "FAIL: $*"
  exit 1
}
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
job_succeeded() { [ "$($KUBECTL -n $NS get job "$1" -o jsonpath='{.status.succeeded}')" = 1 ]; }

finish() {
  local status=$?
  if [ "${KEEP:-}" = 1 ]; then
    echo "Kept the cluster $CLUSTER (KUBECONFIG=$KUBECONFIG) and the :kind images."
  else
    step "Delete the cluster and the images"
    $KIND delete cluster --name "$CLUSTER" || true
    docker image rm needwise-api:kind needwise-web:kind needwise-seed:kind >/dev/null || true
    rm -rf "$WORK"
  fi
  [ $status = 0 ] && echo "All kind checks passed." || echo "kind test failed (exit $status)."
  exit $status
}

if $KIND get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  fail "a kind cluster named $CLUSTER exists already (delete it, or set CLUSTER)"
fi
trap finish EXIT
echo "kind $($KIND version | cut -d' ' -f2), $($HELM version --short), node image $KIND_NODE_IMAGE"

step "Build the images"
docker build -q --build-context ee=ee -t needwise-api:kind apps/api
docker build -q -t needwise-web:kind apps/web
# The seed data image of docs/deploy/kubernetes.md ("Demo data").
printf 'FROM busybox:1.37\nCOPY seed /seed\n' | docker build -q -t needwise-seed:kind -f - .

step "Create the kind cluster $CLUSTER"
sed "s/hostPort: 8680/hostPort: $HTTP_PORT/" deploy/helm/kind/cluster.yaml >"$WORK/cluster.yaml"
$KIND create cluster --name "$CLUSTER" --config "$WORK/cluster.yaml" --image "$KIND_NODE_IMAGE" --wait 5m
$KIND load docker-image --name "$CLUSTER" needwise-api:kind needwise-web:kind needwise-seed:kind
$KUBECTL get nodes

step "ingress-nginx on the control plane"
curl -fsSL -o "$WORK/ingress-nginx.yaml" "$INGRESS_NGINX_URL"
[ "$(sha256 "$WORK/ingress-nginx.yaml")" = "$INGRESS_NGINX_SHA256" ] || fail "ingress-nginx manifest checksum"
$KUBECTL apply -f "$WORK/ingress-nginx.yaml" >/dev/null
# The kind manifest does not pick a node; the published port is on the control plane.
$KUBECTL -n ingress-nginx patch deployment ingress-nginx-controller --type merge \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"ingress-ready":"true","kubernetes.io/os":"linux"}}}}}'
$KUBECTL -n ingress-nginx rollout status deployment/ingress-nginx-controller --timeout 5m
# Its admission webhook answers a little after the controller is ready (the chart's Ingress was
# once refused with "connection refused"): wait until it validates an Ingress.
webhook_check=(create ingress webhook-check -n default --class nginx --rule "check.invalid/*=check:80"
  --dry-run=server -o name)
for _ in $(seq 1 60); do
  $KUBECTL "${webhook_check[@]}" >/dev/null 2>&1 && break
  sleep 2
done
$KUBECTL "${webhook_check[@]}" >/dev/null || fail "the ingress-nginx admission webhook does not answer"

step "A namespace that enforces the restricted Pod Security Standard"
$KUBECTL create namespace $NS
$KUBECTL label namespace $NS pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/enforce-version=latest pod-security.kubernetes.io/warn=restricted

step "helm install (then the hooks: migrate, authz-sync, seed)"
values=(-f "$CHART/ci/kind-values.yaml" --set "config.publicUrl=$BASE_URL")
$HELM install $RELEASE $CHART -n $NS "${values[@]}" --wait --timeout 20m >"$WORK/install.txt" ||
  {
    cat "$WORK/install.txt"
    fail "helm install"
  }
head -6 "$WORK/install.txt"
job_succeeded $RELEASE-migrate || fail "the migrate hook did not succeed"
job_succeeded $RELEASE-authz-sync || fail "the authz-sync hook did not succeed"
job_succeeded $RELEASE-seed || fail "the seed hook did not succeed"
$KUBECTL -n $NS logs job/$RELEASE-migrate -c migrate | grep -c "Running upgrade" |
  xargs printf 'migrate: %s migrations applied\n'
$KUBECTL -n $NS logs job/$RELEASE-seed -c seed | grep -E '^(Analytics|Documents|Authorization|Seeded)'
$KUBECTL -n $NS get pods -o wide

step "helm test"
$HELM test $RELEASE -n $NS --logs | sed -n '/POD LOGS/,$p'

step "API end-to-end tests through the ingress"
(cd apps/api && E2E_BASE_URL=$BASE_URL uv run pytest tests/e2e -q -p no:cacheprovider)

if [ "${SKIP_BROWSER:-}" != 1 ]; then
  step "Browser suite through the ingress"
  make e2e-browser E2E_BASE_URL="$BASE_URL"
fi

step "helm upgrade to two API replicas, polled through the ingress"
migrate_before=$($KUBECTL -n $NS get job $RELEASE-migrate -o jsonpath='{.metadata.uid}')
authz_before=$($KUBECTL -n $NS get job $RELEASE-authz-sync -o jsonpath='{.metadata.uid}')
# Every request must answer 200, and the status page must report every component "ok".
$PYTHON - "$BASE_URL" "$WORK/stop" >"$WORK/poll.txt" <<'EOF' &
import json, sys, time, urllib.request
from pathlib import Path
base, stop = sys.argv[1], Path(sys.argv[2])
results: dict[str, dict[str, int]] = {}
while not stop.exists():
    for path in ("/api/v1/system/status", "/", "/auth/realms/needwise"):
        try:
            with urllib.request.urlopen(base + path, timeout=5) as response:
                ok = response.status == 200
                if path == "/api/v1/system/status":
                    ok = ok and json.loads(response.read())["status"] == "ok"
                result = "ok" if ok else f"bad {response.status}"
        except Exception as error:  # noqa: BLE001 - any failure counts
            result = f"failed {type(error).__name__}"
        results.setdefault(path, {}).setdefault(result, 0)
        results[path][result] += 1
    time.sleep(0.25)
print(json.dumps(results))
sys.exit(0 if all(set(r) == {"ok"} for r in results.values()) else 1)
EOF
poller=$!
upgrade_status=0
$HELM upgrade $RELEASE $CHART -n $NS "${values[@]}" --set api.replicas=2 --wait --timeout 20m \
  >"$WORK/upgrade.txt" || upgrade_status=$?
touch "$WORK/stop"
head -6 "$WORK/upgrade.txt"
[ $upgrade_status = 0 ] || fail "helm upgrade"
poll_status=0
wait $poller || poll_status=$?
echo "requests during the upgrade: $(cat "$WORK/poll.txt")"
[ $poll_status = 0 ] || fail "a request failed during the upgrade"
[ "$($KUBECTL -n $NS get job $RELEASE-migrate -o jsonpath='{.metadata.uid}')" != "$migrate_before" ] ||
  fail "the migrate hook did not run again"
job_succeeded $RELEASE-migrate || fail "the migrate hook did not succeed on upgrade"
[ "$($KUBECTL -n $NS get job $RELEASE-authz-sync -o jsonpath='{.metadata.uid}')" != "$authz_before" ] ||
  fail "the authz-sync hook did not run again"
job_succeeded $RELEASE-authz-sync || fail "the authz-sync hook did not succeed on upgrade"
# The seeded tenant exists now: its store must have been brought up to date.
$KUBECTL -n $NS logs job/$RELEASE-authz-sync -c authz-sync | grep -E '^acme: store ' ||
  fail "the authz-sync hook did not sync the seeded tenant"
job_succeeded $RELEASE-seed || fail "the seed hook did not succeed on upgrade"
[ "$($KUBECTL -n $NS get deployment $RELEASE-api -o jsonpath='{.status.readyReplicas}')" = 2 ] ||
  fail "two API replicas are not ready"
$HELM history $RELEASE -n $NS

step "helm uninstall"
$HELM uninstall $RELEASE -n $NS --wait --timeout 10m
step "Left after the uninstall (kept on purpose; delete them by hand to remove the data)"
$KUBECTL -n $NS get pvc,secret,job --no-headers
for kept in pvc/$RELEASE-objects pvc/data-$RELEASE-postgresql-0 pvc/data-$RELEASE-qdrant-0 \
  pvc/data-$RELEASE-valkey-0 secret/$RELEASE-credentials; do
  $KUBECTL -n $NS get "$kept" >/dev/null || fail "$kept was not kept"
done
