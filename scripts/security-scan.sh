#!/usr/bin/env bash
# Supply-chain scans: the same commands in CI (.github/workflows/security.yml, release.yml) and on
# a laptop (`make scan`). Every scanner runs from a container image pinned by digest, so nothing
# is installed. Policy and current exceptions: docs/security/supply-chain.md.
#
#   scripts/security-scan.sh deps                      known vulnerabilities in uv.lock and
#                                                      pnpm-lock.yaml (osv-scanner; any finding
#                                                      fails unless osv-scanner.toml excepts it)
#   scripts/security-scan.sh fs                        the repository: vulnerabilities,
#                                                      misconfigurations, secrets (Trivy)
#   scripts/security-scan.sh dockerfiles               hadolint
#   scripts/security-scan.sh workflows                 actionlint (with shellcheck), zizmor,
#                                                      and scripts/*.sh through shellcheck
#   scripts/security-scan.sh image REF [REPORT.json]   a local image (Trivy); REPORT gets every
#                                                      finding, the gate only fixable CRITICAL/HIGH
#   scripts/security-scan.sh remote REF PLATFORM       an image in a registry, one platform; set
#                                                      TRIVY_USERNAME/TRIVY_PASSWORD if private
#   scripts/security-scan.sh sbom REF|. OUT.spdx.json  SPDX SBOM of a local image or the repository
#   scripts/security-scan.sh all                       deps, fs, dockerfiles, workflows
set -euo pipefail

# Scanner images, tag for people and digest for the pull. Dependabot does not see these: bump
# tag and digest together by hand (docs/security/supply-chain.md, "Updating the scanners").
OSV_SCANNER="ghcr.io/google/osv-scanner:v2.7.0@sha256:4f3b7e2c64b9d38a5febc5a7e4a3dfef1d1362f726dea7a2ef4762b46469fcd0"
TRIVY="aquasec/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb"
SYFT="anchore/syft:v1.57.0@sha256:3c70e6d5979364cefdc22b51617a78fd264574d3bd6b4f84a351204147025e2a"
HADOLINT="hadolint/hadolint:v2.15.1@sha256:32dac94127fd60b7b7e3fbfc65e1383b9b5e25c9bfd7b8536de7a539fe68a12d"
ACTIONLINT="rhysd/actionlint:1.7.12@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667"
ZIZMOR="ghcr.io/zizmorcore/zizmor:1.32.0@sha256:3014cc8f68f663935f69b3c3e0e41f5ce102b268d97db13db0f62208747f860d"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Trivy's vulnerability database (~130 MB) is kept between runs.
TRIVY_CACHE="${TRIVY_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/workmind-trivy}"
# The gate: what fails a build. Unfixed findings are reported, not gated (nothing to upgrade to).
GATE=(--severity "CRITICAL,HIGH" --ignore-unfixed --exit-code 1 --ignorefile /src/.trivyignore.yaml)
DOCKERFILES=(apps/api/Dockerfile apps/web/Dockerfile)

trivy() {
  mkdir -p "$TRIVY_CACHE"
  docker run --rm -v "$ROOT:/src:ro" -v "$TRIVY_CACHE:/root/.cache/trivy" \
    -e TRIVY_USERNAME -e TRIVY_PASSWORD "$@"
}

# Save a local image to a tar the scanners read; the caller removes the directory.
save_image() {
  local ref="$1" dir="$2"
  docker image inspect "$ref" >/dev/null
  docker save "$ref" -o "$dir/image.tar"
}

usage() {
  sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//' >&2
  exit 2
}

cmd="${1:-}"
[ -n "$cmd" ] || usage
shift

case "$cmd" in
  deps)
    echo "== osv-scanner: apps/api/uv.lock, apps/web/pnpm-lock.yaml" >&2
    docker run --rm -v "$ROOT:/src:ro" "$OSV_SCANNER" scan source \
      --config /src/osv-scanner.toml \
      --lockfile /src/apps/api/uv.lock --lockfile /src/apps/web/pnpm-lock.yaml
    ;;
  fs)
    echo "== Trivy: repository (vulnerabilities incl. dev dependencies, misconfigurations, secrets)" >&2
    # Local installs and build output are not part of the repository.
    trivy "$TRIVY" fs --quiet --scanners vuln,misconfig,secret --include-dev-deps "${GATE[@]}" \
      --skip-dirs /src/apps/api/.venv --skip-dirs /src/apps/web/node_modules \
      --skip-dirs /src/apps/web/.next /src
    ;;
  dockerfiles)
    status=0
    for f in "${DOCKERFILES[@]}"; do
      echo "== hadolint: $f" >&2
      docker run --rm -i "$HADOLINT" hadolint - <"$ROOT/$f" || status=1
    done
    exit "$status"
    ;;
  workflows)
    echo "== actionlint: .github/workflows" >&2
    status=0
    docker run --rm -v "$ROOT:/repo:ro" -w /repo "$ACTIONLINT" -color || status=1
    # zizmor: security audit of the workflows (injection, permissions, pinning: .github/zizmor.yml).
    # With GH_TOKEN set (CI) it also checks that pinned SHAs belong to the action's repository
    # and that no pinned version has a known advisory; without it, offline audits only.
    echo "== zizmor: .github/workflows" >&2
    mode=--offline
    if [ -n "${GH_TOKEN:-}" ]; then mode=--no-progress; fi
    docker run --rm -v "$ROOT:/repo:ro" -w /repo -e GH_TOKEN "$ZIZMOR" \
      --config .github/zizmor.yml --min-severity medium "$mode" .github/workflows || status=1
    echo "== shellcheck: scripts/*.sh" >&2
    docker run --rm -v "$ROOT:/repo:ro" -w /repo --entrypoint sh "$ACTIONLINT" \
      -c 'shellcheck scripts/*.sh' || status=1
    exit "$status"
    ;;
  image)
    [ $# -ge 1 ] || usage
    ref="$1" report="${2:-}"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    save_image "$ref" "$tmp"
    if [ -n "$report" ]; then
      echo "== Trivy: $ref, every finding -> $report" >&2
      trivy -v "$tmp:/in:ro" "$TRIVY" image --quiet --scanners vuln,secret --format json \
        --ignorefile /src/.trivyignore.yaml --input /in/image.tar >"$report"
    fi
    echo "== Trivy gate: $ref (fixable CRITICAL/HIGH vulnerabilities, secrets)" >&2
    trivy -v "$tmp:/in:ro" "$TRIVY" image --quiet --scanners vuln,secret "${GATE[@]}" \
      --input /in/image.tar
    ;;
  remote)
    [ $# -eq 2 ] || usage
    echo "== Trivy gate: $1 ($2)" >&2
    trivy "$TRIVY" image --quiet --scanners vuln,secret --platform "$2" "${GATE[@]}" "$1"
    ;;
  sbom)
    [ $# -eq 2 ] || usage
    target="$1" out="$2"
    if [ "$target" = "." ]; then
      echo "== syft: repository -> $out" >&2
      docker run --rm -v "$ROOT:/src:ro" "$SYFT" scan dir:/src -o spdx-json \
        --source-name workmind --source-version "$(git -C "$ROOT" rev-parse --short HEAD)" \
        --exclude './apps/api/.venv' --exclude './apps/web/node_modules' \
        --exclude './apps/web/.next' >"$out"
    else
      echo "== syft: $target -> $out" >&2
      tmp="$(mktemp -d)"
      trap 'rm -rf "$tmp"' EXIT
      save_image "$target" "$tmp"
      docker run --rm -v "$tmp:/in:ro" "$SYFT" scan docker-archive:/in/image.tar \
        --source-name "${target%%:*}" -o spdx-json >"$out"
    fi
    ;;
  all)
    status=0
    for c in deps fs dockerfiles workflows; do
      "${BASH_SOURCE[0]}" "$c" || status=1
    done
    exit "$status"
    ;;
  *)
    usage
    ;;
esac
