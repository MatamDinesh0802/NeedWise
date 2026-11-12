#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh (docs/operations/backup-restore.md).
#
#   scripts/restore.sh <backup dir> [--force] [--public-url URL] [--no-start]
#   make restore BACKUP=<backup dir>
#
# Into the compose project in DEPLOY_DIR (COMPOSE_PROJECT_NAME / COMPOSE_FILE apply), with the
# images that project runs:
#   1. checks the backup against its checksums, and that the code is not older than the backup
#   2. refuses a target that holds data (tenants, Qdrant points or objects) unless --force
#   3. stops everything except postgres, valkey and qdrant
#   4. recreates the databases from their dumps (roles from globals.sql; passwords stay the
#      target's) and compares WorkMind's row counts with the manifest
#   5. recovers the Qdrant snapshots and compares the point counts
#   6. restores the object storage (an existing tree is moved aside, not deleted) and checks
#      every object's SHA-256; restores the license files
#   7. empties Valkey: its sessions, queued jobs and caches belong to the data being replaced
#   8. starts the stack; migrations run if the backup is older than the code
#   9. with --public-url: points Keycloak's WorkMind client at that address
#  10. `wm authz sync`: brings OpenFGA in line with PostgreSQL
#  11. checks /api/v1/system/status and prints what it found
# --no-start stops after step 7 (start the stack yourself, then run steps 9 and 10).
#
# Environment: as for backup.sh (DEPLOY_DIR, QDRANT_URL, API_URL, OBJECTS_DIR, LICENSE_DIR,
# PGHOST...). Exit codes: 0 restored and verified, 1 failed, 2 refused (non-empty target),
# 3 restored but a check differs from the manifest.
set -euo pipefail
# shellcheck source=lib/common.sh
. "$(dirname "$0")/lib/common.sh"

usage() { sed -n '2,/^set -euo/p' "$0" | sed -e '$d' -e 's/^# \{0,1\}//' >&2; }

BACKUP=""
FORCE=""
NEW_URL=""
START=1
while [[ $# -gt 0 ]]; do
  case $1 in
    --force) FORCE=1 && shift ;;
    --public-url) NEW_URL=${2%/} && shift 2 ;;
    --no-start) START="" && shift ;;
    -h | --help) usage && exit 0 ;;
    -*) usage && die "unknown option: $1" ;;
    *) BACKUP=$1 && shift ;;
  esac
done
[[ -n $BACKUP && -d $BACKUP ]] || {
  usage
  die "give the backup directory"
}
BACKUP=$(cd "$BACKUP" && pwd)

QDRANT_URL=${QDRANT_URL:-http://127.0.0.1:$(env_value QDRANT_PORT 6333)}
API_URL=${API_URL:-http://127.0.0.1:$(env_value API_PORT 8400)}
OBJECTS_DIR=${OBJECTS_DIR:-$DEPLOY_DIR/.data/objects}
LICENSE_DIR=${LICENSE_DIR:-$DEPLOY_DIR/.workmind}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
STARTED=$(date +%s)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/wm-restore.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
umask 022
MISMATCH=""

manifest() { tool get --backup "$BACKUP" "$1"; }

# --- 1. The backup and the code ----------------------------------------------------------------
say "Restoring $BACKUP into the compose project in $DEPLOY_DIR"
tool check --backup "$BACKUP"
APP_DB=$(manifest postgres.app_database)
DATABASES=$(tool get --backup "$BACKUP" --keys postgres.databases)
BACKUP_REVISION=$(manifest alembic_revision)
info "backup of $(manifest created_at): alembic $BACKUP_REVISION, app $(manifest app.version) ($(manifest app.git_describe)), databases: $(echo "$DATABASES" | tr '\n' ' ')"
compose up -d --wait postgres valkey qdrant >/dev/null 2>&1 || die "could not start postgres, valkey and qdrant"
CODE_HEAD=$(compose run --rm --no-deps -T migrate alembic heads 2>/dev/null | awk '/\(head\)/ { print $1 }' | tail -n 1)
info "the code here: alembic head $CODE_HEAD"
if [[ -n $CODE_HEAD && $BACKUP_REVISION > $CODE_HEAD ]]; then
  die "the backup (alembic $BACKUP_REVISION) is newer than this code ($CODE_HEAD): restore it with that version or later"
fi

# --- 2. Refuse to overwrite data ---------------------------------------------------------------
found=()
if psql_value -d "$APP_DB" -c "SELECT to_regclass('public.tenants') IS NOT NULL" 2>/dev/null | grep -qx t; then
  tenants=$(psql_value -d "$APP_DB" -c "SELECT count(*) FROM tenants")
  [[ $tenants -gt 0 ]] && found+=("$tenants tenant(s) in $APP_DB")
fi
points=$(tool qdrant-counts --url "$QDRANT_URL" --include '.*' --exclude '^$')
echo "$points" | grep -Eq ': [1-9]' && found+=("Qdrant points: $points")
[[ -d $OBJECTS_DIR ]] && [[ -n $(find "$OBJECTS_DIR" -type f | head -n 1) ]] && found+=("files in $OBJECTS_DIR")
if [[ ${#found[@]} -gt 0 ]]; then
  for item in "${found[@]}"; do info "target holds: $item"; done
  if [[ -z $FORCE ]]; then
    printf 'refused: the target is not empty. Restoring replaces all of it; run again with --force to do that.\n' >&2
    exit 2
  fi
  warn "--force: replacing the target's data"
fi

# --- 3. Stop what uses the data ----------------------------------------------------------------
say "Stopping the application, Keycloak and OpenFGA"
compose stop caddy web api worker beat keycloak openfga >/dev/null 2>&1 || true

# --- 4. Postgres -------------------------------------------------------------------------------
step=$(date +%s)
say "Postgres"
# Roles first. On a target set up by deploy/postgres/init they exist already ("already exists"
# errors are expected); their attributes are set again, their passwords are left as they are.
pg psql -X -q -v ON_ERROR_STOP=0 -d postgres <"$BACKUP/postgres/globals.sql" >"$WORK/globals.log" 2>&1 || true
info "roles: $(grep -c 'CREATE ROLE' "$BACKUP/postgres/globals.sql") in the backup, $(grep -c 'already exists' "$WORK/globals.log" || true) already there"
for db in $DATABASES; do
  psql_value -d postgres -c "DROP DATABASE IF EXISTS \"$db\" WITH (FORCE)" >/dev/null
  # --create: the database comes back with its owner, encoding and locale from the dump.
  pg pg_restore --create --exit-on-error -d postgres <"$BACKUP/postgres/$db.dump" || die "pg_restore of $db failed"
  info "$db restored"
done
RESTORED_REVISION=$(psql_value -d "$APP_DB" -c "SELECT version_num FROM alembic_version")
psql_value -d "$APP_DB" -c "$ROW_COUNTS_SQL" | tool rows-json >"$WORK/rows.json"
manifest row_counts >"$WORK/rows.expected.json"
tool compare --expected "$WORK/rows.expected.json" --actual "$WORK/rows.json" --what "row counts" || MISMATCH="$MISMATCH rows"
T_POSTGRES=$(since "$step")

# --- 5. Qdrant ---------------------------------------------------------------------------------
step=$(date +%s)
say "Qdrant ($QDRANT_URL)"
tool qdrant-restore --url "$QDRANT_URL" --backup "$BACKUP" || MISMATCH="$MISMATCH points"
T_QDRANT=$(since "$step")

# --- 6. Objects and license --------------------------------------------------------------------
step=$(date +%s)
say "Object storage ($OBJECTS_DIR)"
if [[ -d $OBJECTS_DIR ]] && [[ -n $(find "$OBJECTS_DIR" -mindepth 1 -maxdepth 1 | head -n 1) ]]; then
  mv "$OBJECTS_DIR" "$OBJECTS_DIR.before-restore-$STAMP"
  info "the previous tree is now $OBJECTS_DIR.before-restore-$STAMP (delete it once you are satisfied)"
fi
mkdir -p "$OBJECTS_DIR"
if [[ -f "$BACKUP/objects.tar.gz" ]]; then
  tar -C "$OBJECTS_DIR" -xzf "$BACKUP/objects.tar.gz"
fi
tool verify-tree "$OBJECTS_DIR" "$BACKUP/objects.sha256" || MISMATCH="$MISMATCH objects"
if [[ -d "$BACKUP/license" ]]; then
  mkdir -p "$LICENSE_DIR"
  for file in "$BACKUP"/license/*; do
    name=$(basename "$file")
    if [[ -f "$LICENSE_DIR/$name" ]] && ! cmp -s "$file" "$LICENSE_DIR/$name"; then
      mv "$LICENSE_DIR/$name" "$LICENSE_DIR/$name.before-restore-$STAMP"
    fi
    cp "$file" "$LICENSE_DIR/$name"
    chmod 0644 "$LICENSE_DIR/$name"
  done
  info "license files: $(cd "$BACKUP/license" && echo *)"
fi
T_OBJECTS=$(since "$step")

# --- 7. Valkey ---------------------------------------------------------------------------------
# Sessions (everyone signs in again), queued jobs and cached values all describe the data that
# was just replaced.
compose exec -T valkey valkey-cli FLUSHALL >/dev/null
info "Valkey emptied"

if [[ -z $START ]]; then
  say "Data restored in $(since "$STARTED")s. Start the stack (docker compose up -d --wait), then run"
  info "scripts/keycloak-public-url.sh <url> (if the address changed) and: docker compose run --rm migrate wm authz sync"
  exit 0
fi

# --- 8-10. Start, migrate, adjust ----------------------------------------------------------------
step=$(date +%s)
say "Starting the stack (migrations run first)"
compose up -d --wait >/dev/null 2>&1 || {
  compose ps -a >&2
  die "the stack did not become healthy: docker compose logs migrate api"
}
MIGRATED_REVISION=$(psql_value -d "$APP_DB" -c "SELECT version_num FROM alembic_version")
if [[ $MIGRATED_REVISION != "$RESTORED_REVISION" ]]; then
  info "migrated: alembic $RESTORED_REVISION -> $MIGRATED_REVISION"
else
  info "alembic $MIGRATED_REVISION: no migration needed"
fi
T_START=$(since "$step")
if [[ -n $NEW_URL ]]; then
  "$REPO_ROOT/scripts/keycloak-public-url.sh" "$NEW_URL"
fi
say "Authorization: OpenFGA from PostgreSQL"
compose run --rm --no-deps -T migrate wm authz sync 2>&1 | sed 's/^/    /' >&2 ||
  warn "wm authz sync failed: run it again (docker compose run --rm migrate wm authz sync)"

# --- 11. Checks --------------------------------------------------------------------------------
say "Checks"
if tool http-json "$API_URL/api/v1/system/status" >"$WORK/status.json"; then
  "${TOOL[0]}" -c 'import json,sys; s=json.load(open(sys.argv[1])); print("    system status: %s, version %s (%s)" % (s["status"], s["version"], ", ".join("%s %s" % (c["name"], c["status"]) for c in s["components"])))' "$WORK/status.json" >&2
  grep -q '"status": "ok"' "$WORK/status.json" || MISMATCH="$MISMATCH status"
else
  warn "no answer from $API_URL/api/v1/system/status"
  MISMATCH="$MISMATCH status"
fi
waiting=$(psql_value -d "$APP_DB" -c "SELECT count(*) FROM documents WHERE status IN ('pending', 'processing')" 2>/dev/null || echo 0)
if [[ $waiting -gt 0 ]]; then
  warn "$waiting document(s) were queued or being indexed at backup time; their jobs were in Valkey."
  info "re-index them: docker compose run --rm worker wm reindex <tenant slug> (or per document in the UI)"
fi
say "Restored in $(since "$STARTED")s (postgres ${T_POSTGRES}s, qdrant ${T_QDRANT}s, objects ${T_OBJECTS}s, start ${T_START}s)"
if [[ -n $MISMATCH ]]; then
  warn "differences from the manifest:$MISMATCH (see above)"
  exit 3
fi
