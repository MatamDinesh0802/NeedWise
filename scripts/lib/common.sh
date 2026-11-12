# shellcheck shell=bash
# Shared by scripts/backup.sh and scripts/restore.sh. Bash 3.2 compatible (macOS /bin/bash).
#
# Which deployment: the docker compose project in DEPLOY_DIR (default: this repository), with
# the usual COMPOSE_PROJECT_NAME / COMPOSE_FILE variables if you set them. Ports and the
# Keycloak admin login come from the environment or DEPLOY_DIR/.env, as compose reads them.
#
# Postgres tools run inside the compose `postgres` service as the postgres superuser (local
# socket, no password). With PGHOST set they run in a throwaway client container that connects
# over TCP instead (a managed Postgres, or a stack you may not exec into): PGHOST, PGPORT,
# PGUSER (default postgres) and PGPASSWORD are the standard libpq variables.

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
DEPLOY_DIR=${DEPLOY_DIR:-$REPO_ROOT}
TOOL=("${PYTHON:-python3}" "$REPO_ROOT/scripts/lib/backup_tool.py")
PG_CLIENT_IMAGE=${PG_CLIENT_IMAGE:-postgres:18.6}

say() { printf '==> %s\n' "$*" >&2; }
info() { printf '    %s\n' "$*" >&2; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

tool() { "${TOOL[@]}" "$@"; }

# A value from the environment, else from DEPLOY_DIR/.env (only the named variable is read),
# else the default. Handles `NAME=value  # comment` and quoted values.
env_value() {
  local name=$1 default=${2-} line value
  if [[ -n "${!name:-}" ]]; then
    printf '%s' "${!name}"
    return
  fi
  if [[ -f "$DEPLOY_DIR/.env" ]]; then
    line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?${name}=" "$DEPLOY_DIR/.env" | tail -n 1 || true)
    if [[ -n $line ]]; then
      value=${line#*=}
      case $value in
        \"*) value=${value#\"} && value=${value%%\"*} ;;
        \'*) value=${value#\'} && value=${value%%\'*} ;;
        *) value=$(printf '%s' "$value" | sed -E -e 's/[[:space:]]+#.*$//' -e 's/[[:space:]]*$//') ;;
      esac
      if [[ -n $value ]]; then
        printf '%s' "$value"
        return
      fi
    fi
  fi
  printf '%s' "$default"
}

compose() { (cd "$DEPLOY_DIR" && docker compose "$@"); }

pg() {
  if [[ -n "${PGHOST:-}" ]]; then
    docker run --rm -i --add-host host.docker.internal:host-gateway \
      -e PGHOST -e "PGPORT=${PGPORT:-5432}" -e "PGUSER=${PGUSER:-postgres}" -e PGPASSWORD \
      "$PG_CLIENT_IMAGE" "$@"
  else
    compose exec -T -e PGUSER=postgres postgres "$@"
  fi
}

psql_value() { pg psql -X -A -t -q -v ON_ERROR_STOP=1 "$@"; }

running_services() { compose ps --status running --services 2>/dev/null || true; }

is_running() { running_services | grep -qx "$1"; }

# The api and worker containers run as uid 10001 and write new objects into the bind-mounted
# object storage. On Linux a bind mount keeps the host's owners, so a tree created or extracted
# by another user must be handed to that uid (Docker Desktop on macOS maps owners itself).
own_objects() {
  if [[ $(uname -s) == Linux ]]; then
    docker run --rm -v "$1:/objects" "${ALPINE_IMAGE:-alpine:3.22}" chown -R "${OBJECTS_UID:-10001}" /objects
  fi
}

# Elapsed seconds since $1 (a `date +%s` value).
since() { echo $(($(date +%s) - $1)); }

# The row counts of every table in WorkMind's public schema, as `table|count` lines. Run as a
# superuser, so Row-Level Security does not hide other tenants' rows.
# shellcheck disable=SC2034 # used by the scripts that source this file
ROW_COUNTS_SQL="SELECT table_name || '|' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1;"
