#!/usr/bin/env bash
# Back up a WorkMind deployment (docs/operations/backup-restore.md).
#
#   scripts/backup.sh [--output DIR] [--pause | --offline | --online] [--keep N] [--no-compose]
#   make backup                      the same, into ./backups
#
# Writes DIR/workmind-<UTC time>/ (mode 0700; default DIR: ./backups):
#   postgres/<db>.dump         pg_dump, custom format: workmind, keycloak, openfga (PG_DATABASES)
#   postgres/globals.sql       roles and their attributes, without passwords
#   qdrant/<name>.snapshot     a snapshot of every WorkMind collection (chunks_*)
#   objects.tar.gz             the object storage tree (uploads, recordings, analytics data)
#   objects.sha256             SHA-256 of every object, checked after a restore
#   license/                   license.key and dev-public.key, when present
#   manifest.json              versions, alembic revision, embedding model, row and point
#                              counts, sizes and checksums; SHA256SUMS for `shasum -c`
#
# Consistency:
#   --pause    (default) stops worker and beat while it runs, so no ingestion, sync or scheduled
#              job writes; people can keep working. WorkMind's database is dumped from one
#              snapshot, and the row counts in the manifest come from that same snapshot.
#   --offline  also stops api and web: nothing changes during the backup (downtime).
#   --online   stops nothing: what changes during the backup may be missing or half-present
#              across the stores (repaired after a restore with `wm reindex`, `wm authz sync`).
#
# Not backed up, on purpose: Valkey (sessions, the job queue, caches: see the docs for what that
# loses), the beat schedule volume, the model caches, and your secrets (.env,
# SECRETS_ENCRYPTION_KEY): keep those apart from the backups.
#
# Environment: DEPLOY_DIR, COMPOSE_PROJECT_NAME/COMPOSE_FILE, PGHOST/PGPORT/PGUSER/PGPASSWORD
# (see scripts/lib/common.sh), QDRANT_URL, QDRANT_API_KEY, API_URL, OBJECTS_DIR, LICENSE_DIR,
# PG_DATABASES, QDRANT_INCLUDE/QDRANT_EXCLUDE (regular expressions), STOP_TIMEOUT.
set -euo pipefail
# shellcheck source=lib/common.sh
. "$(dirname "$0")/lib/common.sh"

usage() { sed -n '2,/^set -euo/p' "$0" | sed -e '$d' -e 's/^# \{0,1\}//' >&2; }

OUTPUT=${BACKUP_ROOT:-$DEPLOY_DIR/backups}
MODE=pause
KEEP=0
NO_COMPOSE=${NO_COMPOSE:-}
while [[ $# -gt 0 ]]; do
  case $1 in
    -o | --output) OUTPUT=$2 && shift 2 ;;
    --pause | --offline | --online) MODE=${1#--} && shift ;;
    --keep) KEEP=$2 && shift 2 ;;
    --no-compose) NO_COMPOSE=1 && shift ;;
    -h | --help) usage && exit 0 ;;
    *) usage && die "unknown option: $1" ;;
  esac
done
if [[ -n $NO_COMPOSE ]]; then
  [[ -n ${PGHOST:-} ]] || die "--no-compose needs PGHOST (and PGPASSWORD) to reach Postgres"
  [[ $MODE == online ]] || die "--no-compose can only take --online backups (it stops nothing)"
fi

PG_DATABASES=${PG_DATABASES:-workmind keycloak openfga}
APP_DB=${APP_DB:-workmind}
QDRANT_URL=${QDRANT_URL:-http://127.0.0.1:$(env_value QDRANT_PORT 6333)}
API_URL=${API_URL:-http://127.0.0.1:$(env_value API_PORT 8400)}
OBJECTS_DIR=${OBJECTS_DIR:-$DEPLOY_DIR/.data/objects}
LICENSE_DIR=${LICENSE_DIR:-$DEPLOY_DIR/.workmind}
STOP_TIMEOUT=${STOP_TIMEOUT:-120}

umask 077
STARTED=$(date +%s)
CREATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TARGET="$OUTPUT/workmind-$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$TARGET/tmp"
mkdir -p "$TARGET/postgres" "$TARGET/qdrant" "$TMP"

say "Backup of $(if [[ -n $NO_COMPOSE ]]; then echo "$PGHOST:${PGPORT:-5432}"; else echo "the compose project in $DEPLOY_DIR"; fi) to $TARGET ($MODE)"
command -v docker >/dev/null || die "docker is required"
command -v "${PYTHON:-python3}" >/dev/null || die "python3 is required"
PG_VERSION=$(psql_value -d postgres -c 'SHOW server_version') || die "cannot reach Postgres"
tool http-json "$QDRANT_URL/collections" >/dev/null || die "cannot reach Qdrant at $QDRANT_URL"

# --- What is running ---------------------------------------------------------------------------
API_IMAGE=""
if [[ -z $NO_COMPOSE ]] && is_running api; then
  container=$(compose ps -q api | head -n 1)
  API_IMAGE=$(docker inspect --format '{{.Config.Image}} {{.Image}}' "$container" 2>/dev/null || true)
  # Version and embedding settings as the running API sees them; getattr: older versions lack some.
  compose exec -T api python - >"$TMP/app.json" 2>/dev/null <<'PY' || rm -f "$TMP/app.json"
import json
import workmind
from workmind.core.config import Settings
s = Settings()
print(json.dumps({
    "version": workmind.__version__,
    "app_env": s.app_env,
    "public_url": s.public_url,
    "embedding_model": getattr(s, "embedding_model", None),
    "embedding_dimensions": getattr(s, "embedding_dimensions", None),
    "qdrant_collection": getattr(s, "qdrant_collection", None),
    "from": "settings of the running api container",
}))
PY
fi
if [[ ! -s "$TMP/app.json" ]]; then
  tool http-json "$API_URL/api/v1/system/status" >"$TMP/status.json" 2>/dev/null || true
fi
# The code checked out in DEPLOY_DIR (set GIT_COMMIT when the images come from elsewhere).
GIT_COMMIT=${GIT_COMMIT:-$(git -C "$DEPLOY_DIR" rev-parse HEAD 2>/dev/null || true)}
GIT_DESCRIBE=${GIT_DESCRIBE:-$(git -C "$DEPLOY_DIR" describe --tags --always --dirty 2>/dev/null || true)}

# --- Pause the background writers ------------------------------------------------------------
STOPPED=()
resume() {
  if [[ ${#STOPPED[@]} -gt 0 ]]; then
    say "Starting ${STOPPED[*]} again"
    compose start "${STOPPED[@]}" >/dev/null 2>&1 || warn "could not start ${STOPPED[*]}: start them by hand"
  fi
}
trap resume EXIT
candidates=()
case $MODE in
  pause) candidates=(beat worker) ;;
  offline) candidates=(beat worker web api) ;;
esac
for service in ${candidates[@]+"${candidates[@]}"}; do
  if is_running "$service"; then STOPPED+=("$service"); fi
done
PAUSED_AT=$(date +%s)
if [[ ${#STOPPED[@]} -gt 0 ]]; then
  say "Stopping ${STOPPED[*]} (running jobs get up to ${STOP_TIMEOUT}s to finish)"
  compose stop -t "$STOP_TIMEOUT" "${STOPPED[@]}" >/dev/null 2>&1
fi

# --- Postgres ----------------------------------------------------------------------------------
say "Postgres $PG_VERSION: $PG_DATABASES"
step=$(date +%s)
# WorkMind's database: one exported snapshot shared by pg_dump and the counting queries, so the
# manifest describes exactly what the dump holds, even while people keep working.
fifo=$(mktemp -d "$TMP/psql.XXXX")
mkfifo "$fifo/in" "$fifo/out"
psql_value -d "$APP_DB" <"$fifo/in" >"$fifo/out" &
psql_pid=$!
exec 3>"$fifo/in" 4<"$fifo/out"
query() {
  printf '%s\n%s\n' "$1" '\echo __END__' >&3
  local line
  while IFS= read -r line <&4; do
    [[ $line == __END__ ]] && return 0
    printf '%s\n' "$line"
  done
  return 1
}
echo "BEGIN ISOLATION LEVEL REPEATABLE READ, READ ONLY; SELECT pg_export_snapshot();" >&3
read -r SNAPSHOT <&4 || die "could not open a snapshot of $APP_DB"
pg pg_dump -Fc --snapshot="$SNAPSHOT" -d "$APP_DB" >"$TARGET/postgres/$APP_DB.dump"
ALEMBIC=$(query "SELECT version_num FROM alembic_version;") || die "no alembic_version in $APP_DB"
query "$ROW_COUNTS_SQL" | tool rows-json >"$TMP/rows.json"
if grep -q '"documents"' "$TMP/rows.json"; then
  query "SELECT status || '|' || count(*) FROM documents GROUP BY status ORDER BY 1;" |
    tool rows-json >"$TMP/documents.json"
fi
echo "COMMIT;" >&3
exec 3>&-
wait "$psql_pid"
exec 4<&-
info "$APP_DB: alembic $ALEMBIC, $(($(wc -c <"$TARGET/postgres/$APP_DB.dump"))) bytes"

for db in $PG_DATABASES; do
  [[ $db == "$APP_DB" ]] && continue
  pg pg_dump -Fc -d "$db" >"$TARGET/postgres/$db.dump"
  info "$db: $(($(wc -c <"$TARGET/postgres/$db.dump"))) bytes"
done
# shellcheck disable=SC2086 # one word per database
in_list=$(printf "'%s'," $PG_DATABASES)
psql_value -d postgres -c \
  "SELECT datname || '|' || pg_get_userbyid(datdba) FROM pg_database WHERE datname IN (${in_list%,}) ORDER BY 1" |
  tool rows-json --strings >"$TMP/owners.json"
# Roles (workmind_owner, workmind_app NOBYPASSRLS, keycloak, openfga) without their password
# hashes: the restore target creates the roles with its own passwords (deploy/postgres/init).
pg pg_dumpall --globals-only --no-role-passwords >"$TARGET/postgres/globals.sql"
T_POSTGRES=$(since "$step")

# --- Qdrant ------------------------------------------------------------------------------------
say "Qdrant snapshots ($QDRANT_URL)"
step=$(date +%s)
tool qdrant-backup --url "$QDRANT_URL" --out "$TARGET/qdrant"
T_QDRANT=$(since "$step")

# --- Objects and license -----------------------------------------------------------------------
# After the database: objects are written before the rows that point at them commit, and are
# content-addressed (never rewritten), so every object the dump refers to is already on disk.
step=$(date +%s)
OBJECT_FILES=0
if [[ -d $OBJECTS_DIR ]]; then
  say "Object storage ($OBJECTS_DIR)"
  tool hash-tree "$OBJECTS_DIR" >"$TARGET/objects.sha256"
  cut -c 67- "$TARGET/objects.sha256" >"$TMP/objects.list"
  OBJECT_FILES=$(($(wc -l <"$TMP/objects.list")))
  if [[ $OBJECT_FILES -gt 0 ]]; then
    COPYFILE_DISABLE=1 tar -C "$OBJECTS_DIR" -czf "$TARGET/objects.tar.gz" -T "$TMP/objects.list"
  fi
  info "$OBJECT_FILES files"
else
  warn "no object storage at $OBJECTS_DIR"
  : >"$TARGET/objects.sha256"
fi
LICENSE_FILES=""
for name in license.key dev-public.key; do # never dev-signing.key: a private key
  if [[ -f "$LICENSE_DIR/$name" ]]; then
    mkdir -p "$TARGET/license"
    cp "$LICENSE_DIR/$name" "$TARGET/license/$name"
    LICENSE_FILES="$LICENSE_FILES $name"
  fi
done
[[ -n $LICENSE_FILES ]] && info "license:$LICENSE_FILES"
T_OBJECTS=$(since "$step")

resume
WAS_STOPPED=${STOPPED[*]+${STOPPED[*]}}
STOPPED=()
PAUSED_S=0
[[ -n $WAS_STOPPED ]] && PAUSED_S=$(since "$PAUSED_AT")

# --- Manifest ----------------------------------------------------------------------------------
tool kv-json \
  "created_at=$CREATED_AT" "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" "mode=$MODE" \
  "host=$(hostname)" "deploy_dir=$DEPLOY_DIR" "compose_project=${COMPOSE_PROJECT_NAME:-}" \
  "git_commit=$GIT_COMMIT" "git_describe=$GIT_DESCRIBE" "api_image=$API_IMAGE" \
  "pg_version=$PG_VERSION" "pg_source=${PGHOST:+tcp://$PGHOST:${PGPORT:-5432}}" \
  "qdrant_url=$QDRANT_URL" "alembic_revision=$ALEMBIC" "app_database=$APP_DB" \
  "databases=$PG_DATABASES" "license_files=$LICENSE_FILES" "stopped=$WAS_STOPPED" \
  "seconds_postgres:=$T_POSTGRES" "seconds_qdrant:=$T_QDRANT" "seconds_objects:=$T_OBJECTS" \
  "seconds_paused:=$PAUSED_S" "seconds_total:=$(since "$STARTED")" >"$TMP/info.json"
tool finish --backup "$TARGET"
rm -rf "$TMP"
tool check --backup "$TARGET" >/dev/null 2>&1 || die "the backup in $TARGET does not match its own checksums"
say "Done in $(since "$STARTED")s: $TARGET ($(du -sh "$TARGET" | cut -f 1))"

if [[ $KEEP -gt 0 ]]; then
  # Names sort by time; everything but the newest KEEP complete backups goes.
  find "$OUTPUT" -mindepth 1 -maxdepth 1 -type d -name 'workmind-*' -exec test -f '{}/manifest.json' ';' -print |
    sort | awk -v keep="$KEEP" '{ line[NR] = $0 } END { for (i = 1; i <= NR - keep; i++) print line[i] }' |
    while IFS= read -r dir; do
      info "removing old backup $dir (--keep $KEEP)"
      rm -rf "$dir"
    done
fi
printf '%s\n' "$TARGET"
