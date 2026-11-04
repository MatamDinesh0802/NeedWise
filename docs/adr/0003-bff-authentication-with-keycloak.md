# 0003. Backend-for-Frontend authentication with Keycloak

- Status: Accepted
- Date: 2026-11-02

## Context
Enterprises need SSO (Okta, Entra ID, Google Workspace, SAML, LDAP), MFA and central user
lifecycle. Storing OAuth tokens in browser JavaScript exposes them to XSS. The same
identity must work for the web UI, API clients and MCP clients.

## Decision
- Keycloak is the identity provider: login, MFA, federation, organizations. WorkMind never
  stores passwords.
- The FastAPI backend acts as a Backend-for-Frontend: it runs the OIDC Authorization Code flow
  with PKCE, keeps tokens server-side in Valkey, and gives the browser only an opaque
  `HttpOnly; Secure; SameSite=Lax` session cookie.
- UI, API and Keycloak share one origin behind Caddy, so the cookie works without CORS.
- Application permissions (departments, groups, document access) live in WorkMind, not in
  Keycloak (see ADR 0006).
- Machine clients use scoped personal access tokens or Keycloak service accounts.

## Consequences
- Tokens are never readable by browser JavaScript.
- One auth path for every client type, with a single `RequestContext` downstream.
- Keycloak adds about 1 GB of memory to the stack and must be operated in production.
