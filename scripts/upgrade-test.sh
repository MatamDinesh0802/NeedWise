#!/usr/bin/env bash
# Upgrade test (docs/operations/upgrade.md): an older release, seeded and used, upgraded in place
# to this code the way an operator would, then checked.
#
#   scripts/upgrade-test.sh <from tag> [<to ref>]      e.g.  scripts/upgrade-test.sh v0.1.0
#   make upgrade-test FROM=v0.1.0
#
#  1. `git archive <from tag>` into a work directory (not a git checkout), started as compose
#     project "workmind-upgrade" with its own image tags (an override file) and its own ports
#     (the defaults + UPGRADE_PORT_OFFSET, 200: http://localhost:8680), then seeded
#  2. data created through the old API (scripts/lib/stack_checks.py upgrade-before)
#  3. a backup with scripts/backup.sh, as the upgrade guide asks
#  4. the upgrade, in place: `docker compose down` (volumes stay), the old release's files
#     replaced by <to ref>'s (default HEAD; .data/ stays), `mkdir -p .data/objects .workmind`,
#     `docker compose up -d --build --wait` (migrations run first), `wm authz sync`
#  5. checks (stack_checks.py upgrade-after): alembic at head, sign-ins, the old data and token,
#     the audit chain, edition, usage, upload -> ingestion -> search, the permission gate
#  6. teardown: `down -v`, the work directory and the images it built (KEEP=1 keeps them)
#
# Embeddings: EMBEDDINGS=host (default) uses the server on the host's port 8091 (make
# embeddings); EMBEDDINGS=container runs the compose `embeddings` profile (CI, any OS).
# Needs: docker with compose v2, git, python3, curl.
set -euo pipefail
# shellcheck source=lib/common.sh
. "$(dirname "$0")/lib/common.sh"

FROM=${1:-}
TO=${2:-HEAD}
[[ -n $FROM ]] || die "usage: $0 <from tag> [<to ref>]   e.g. $0 v0.1.0"
git -C "$REPO_ROOT" rev-parse -q --verify "$FROM^{commit}" >/dev/null || die "unknown tag or commit: $FROM"
TO_COMMIT=$(git -C "$REPO_ROOT" rev-parse "$TO^{commit}")
WORK=${UPGRADE_WORK_DIR:-${TMPDIR:-/tmp}/wm-upgrade-$FROM}
WORK=${WORK%/}
EMBEDDINGS=${EMBEDDINGS:-host}
OFFSET=${UPGRADE_PORT_OFFSET:-200}
STATE="$WORK/.upgrade-test"
STARTED=$(date +%s)
[[ ! -e $WORK ]] || die "$WORK exists: remove it or set UPGRADE_WORK_DIR"

export COMPOSE_PROJECT_NAME=${UPGRADE_PROJECT:-workmind-upgrade}
export COMPOSE_FILE="compose.yaml:.upgrade-test/images.override.yaml"
export HTTP_PORT=$((8480 + OFFSET)) API_PORT=$((8400 + OFFSET)) WEB_PORT=$((3400 + OFFSET))
export POSTGRES_PORT=$((5432 + OFFSET)) VALKEY_PORT=$((6379 + OFFSET)) QDRANT_PORT=$((6333 + OFFSET))
export OPENFGA_PORT=$((8401 + OFFSET)) MAILPIT_PORT=$((8025 + OFFSET))
export PUBLIC_URL="http://localhost:$HTTP_PORT"
if [[ $EMBEDDINGS == container ]]; then
  export COMPOSE_PROFILES=embeddings EMBEDDING_BASE_URL=http://embeddings:8080/v1
elif ! curl -fsS http://127.0.0.1:8091/health >/dev/null 2>&1; then
  die "no embedding server on the host's port 8091: run make embeddings, or EMBEDDINGS=container"
fi
DEPLOY_DIR=$WORK # common.sh's compose() runs here
IMAGES=()

cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then
    warn "failed (exit $status); the stack as it is:"
    compose ps -a >&2 || true
    compose logs --no-color --tail 40 migrate api >&2 2>/dev/null || true
  fi
  if [[ -n ${KEEP:-} ]]; then
    say "KEEP=1: left running as $COMPOSE_PROJECT_NAME in $WORK"
    return
  fi
  say "Teardown"
  if [[ -d $WORK ]]; then
    compose down -v --remove-orphans >/dev/null 2>&1 || true
    # Files the containers wrote may belong to their user (Linux): delete them from a container.
    rm -rf "$WORK" 2>/dev/null ||
      docker run --rm -v "$WORK:/work" alpine:3.22 rm -rf /work/.data /work/.workmind && rm -rf "$WORK"
  fi
  for image in ${IMAGES[@]+"${IMAGES[@]}"}; do docker image rm "$image" >/dev/null 2>&1 || true; done
}
trap cleanup EXIT

# An override that gives the app services their own image tags (never workmind-*:dev).
write_override() {
  mkdir -p "$STATE"
  COMPOSE_FILE=compose.yaml compose --profile '*' config --format json |
    "${PYTHON:-python3}" -c '
import json, sys
tag = sys.argv[1]
print("services:")
for name, service in sorted(json.load(sys.stdin)["services"].items()):
    repository = service.get("image", "").split(":")[0]
    if repository in ("workmind-api", "workmind-web"):
        print(f"  {name}:\n    image: {repository}:{tag}")
' "$1" >"$STATE/images.override.yaml"
  IMAGES+=("workmind-api:$1" "workmind-web:$1")
}

psql_app() { compose exec -T postgres psql -U postgres -d workmind -X -A -t -q -c "$1"; }

# Runs a command with its output in $STATE/<name>.log; on failure, shows the end of the log.
quiet() {
  local name=$1
  shift
  "$@" >"$STATE/$name.log" 2>&1 || {
    tail -n 40 "$STATE/$name.log" >&2
    die "$name failed (the whole log: $STATE/$name.log, with KEEP=1)"
  }
}

# --- 1. The old release ------------------------------------------------------------------------
say "Old release $FROM in $WORK (project $COMPOSE_PROJECT_NAME, $PUBLIC_URL)"
mkdir -p "$WORK"
git -C "$REPO_ROOT" archive "$FROM" | tar -x -C "$WORK"
write_override upgrade-old
step=$(date +%s)
quiet build-old compose build
T_OLD_BUILD=$(since "$step")
step=$(date +%s)
quiet up-old compose up -d --wait
quiet seed-old compose --profile tools run --rm seed
T_OLD_UP=$(since "$step")
OLD_REVISION=$(psql_app "SELECT version_num FROM alembic_version")
OLD_VERSION=$(tool http-json "http://127.0.0.1:$API_PORT/api/v1/system/status" | sed -n 's/.*"version": "\([^"]*\)".*/\1/p')
info "$FROM: version $OLD_VERSION, alembic $OLD_REVISION (built in ${T_OLD_BUILD}s, up and seeded in ${T_OLD_UP}s)"

# --- 2. Data made with the old version ---------------------------------------------------------
say "Data through the $FROM API"
tool_checks() { "${PYTHON:-python3}" "$REPO_ROOT/scripts/lib/stack_checks.py" "$@"; }
tool_checks upgrade-before "$PUBLIC_URL" "$STATE/state.json"
psql_app "$ROW_COUNTS_SQL" | tool rows-json >"$STATE/rows-before.json"

# --- 3. Back up first --------------------------------------------------------------------------
say "Backup before the upgrade"
step=$(date +%s)
DEPLOY_DIR=$WORK QDRANT_URL="http://127.0.0.1:$QDRANT_PORT" API_URL="http://127.0.0.1:$API_PORT" \
  "$REPO_ROOT/scripts/backup.sh" --output "$STATE/backups" >"$STATE/backup.log" 2>&1 || {
  cat "$STATE/backup.log" >&2
  die "backup of $FROM failed"
}
T_BACKUP=$(since "$step")
info "backup in ${T_BACKUP}s: $(tail -n 1 "$STATE/backup.log")"

# --- 4. Upgrade in place -----------------------------------------------------------------------
say "Upgrade to $TO ($(git -C "$REPO_ROOT" describe --tags --always "$TO_COMMIT"))"
step=$(date +%s)
compose down >/dev/null 2>&1 # volumes stay
# Replace the release's files, as `git checkout <new tag>` would in a deployment directory.
# Untracked files stay: .data/, .workmind/, .env, and this test's own state.
git -C "$REPO_ROOT" ls-tree -r --name-only "$FROM" | (cd "$WORK" && while IFS= read -r file; do rm -f "./$file"; done)
find "$WORK" -mindepth 1 -type d -empty -not -path "$STATE*" -delete
git -C "$REPO_ROOT" archive "$TO_COMMIT" | tar -x -C "$WORK"
mkdir -p "$WORK/.data/objects" "$WORK/.workmind" # new bind mounts (make up does this)
own_objects "$WORK/.data/objects"
write_override upgrade
quiet build-new compose build
T_BUILD=$(since "$step")
step=$(date +%s)
quiet up-new compose up -d --wait
T_UP=$(since "$step")
NEW_REVISION=$(psql_app "SELECT version_num FROM alembic_version")
CODE_HEAD=$(compose run --rm --no-deps -T migrate alembic heads 2>/dev/null | awk '/\(head\)/ { print $1 }' | tail -n 1)
info "migrated alembic $OLD_REVISION -> $NEW_REVISION (code head $CODE_HEAD); build ${T_BUILD}s, up ${T_UP}s"
[[ $NEW_REVISION == "$CODE_HEAD" ]] || die "the database is at $NEW_REVISION, not at the code's head $CODE_HEAD"
compose run --rm --no-deps -T migrate wm authz sync 2>&1 | grep -v "Container" | sed 's/^/    /' >&2

# --- 5. Checks ---------------------------------------------------------------------------------
say "Checks after the upgrade"
psql_app "$ROW_COUNTS_SQL" | tool rows-json >"$STATE/rows-after.json"
"${PYTHON:-python3}" - "$STATE/rows-before.json" "$STATE/rows-after.json" <<'PY' >&2
import json, sys
before, after = (json.load(open(path)) for path in sys.argv[1:3])
lost = {t: (n, after.get(t)) for t, n in before.items() if t != "alembic_version" and after.get(t, 0) < n}
added = sorted(set(after) - set(before))
print(f"    tables: {len(before)} before, {len(after)} after ({len(added)} new); rows lost: {lost or 'none'}")
sys.exit(1 if lost else 0)
PY
tool_checks upgrade-after "$PUBLIC_URL" "$STATE/state.json"
say "Upgrade $FROM -> $TO passed in $(since "$STARTED")s"
