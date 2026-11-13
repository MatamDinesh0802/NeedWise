#!/usr/bin/env bash
# Point NeedWise's Keycloak client at a new public URL (docs/operations/backup-restore.md).
#
#   scripts/keycloak-public-url.sh https://needwise.example.com
#
# Keycloak keeps the address people sign in through in its database, in NeedWise's client: the
# redirect URI (<url>/api/auth/callback) and the post-logout redirect (<url>/*). They were set
# when the realm was first imported (deploy/keycloak/realm-needwise.json, from PUBLIC_URL), and a
# restore brings back the source's values. On another address, sign-in then stops on Keycloak's
# "Invalid parameter: redirect_uri" page until this script updates them.
#
# Also set PUBLIC_URL to the same URL in .env (it sets Keycloak's hostname and the API's
# redirect URI and cookies) and recreate the stack: docker compose up -d.
#
# Logs in to Keycloak's master realm as KEYCLOAK_ADMIN_USER / KEYCLOAK_ADMIN_PASSWORD (from the
# environment or .env; after a restore these are the SOURCE's admin credentials, which live in
# the restored database). The password reaches kcadm through the environment, not arguments.
set -euo pipefail
# shellcheck source=lib/common.sh
. "$(dirname "$0")/lib/common.sh"

URL=${1:-}
[[ $URL =~ ^https?://[^/]+$|^https?://[^/]+/$ ]] || die "usage: $0 <public URL, e.g. https://needwise.example.com>"
URL=${URL%/}
REALM=${KEYCLOAK_REALM:-needwise}
CLIENT_ID=${OIDC_CLIENT_ID:-needwise-api}
ADMIN_USER=$(env_value KEYCLOAK_ADMIN_USER admin)
KC_CLI_PASSWORD=$(env_value KEYCLOAK_ADMIN_PASSWORD admin)
export KC_CLI_PASSWORD

kcadm() {
  compose exec -T -e KC_CLI_PASSWORD keycloak /opt/keycloak/bin/kcadm.sh "$@" --config /tmp/nw-kcadm.config
}

say "Keycloak: client $CLIENT_ID of realm $REALM -> $URL"
kcadm config credentials --server http://localhost:8080/auth --realm master --user "$ADMIN_USER" >/dev/null ||
  die "could not log in to Keycloak as $ADMIN_USER (after a restore: the source's admin password)"
id=$(kcadm get clients -r "$REALM" -q "clientId=$CLIENT_ID" --fields id --format csv --noquotes | tr -d '\r' | head -n 1)
[[ -n $id ]] || die "client $CLIENT_ID not found in realm $REALM"
before=$(kcadm get "clients/$id" -r "$REALM" --fields redirectUris --format csv --noquotes | tr -d '\r')
kcadm update "clients/$id" -r "$REALM" \
  -s "redirectUris=[\"$URL/api/auth/callback\"]" \
  -s "attributes.\"post.logout.redirect.uris\"=$URL/*"
after=$(kcadm get "clients/$id" -r "$REALM" --fields redirectUris --format csv --noquotes | tr -d '\r')
compose exec -T keycloak rm -f /tmp/nw-kcadm.config
info "redirect URIs: $before -> $after"
current=$(env_value PUBLIC_URL http://localhost:8480)
if [[ ${current%/} != "$URL" ]]; then
  warn "PUBLIC_URL is $current here: set it to $URL (.env) and run docker compose up -d"
fi
