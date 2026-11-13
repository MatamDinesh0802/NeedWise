# 0011. The open-core boundary: Ed25519 license keys and an extension registry

- Status: Accepted
- Date: 2026-11-12
- Context documents: [ADR 0004](0004-apache-2-open-core-and-dco.md); ROADMAP §4 and §5;
  [docs/enterprise.md](../enterprise.md); [`ee/README.md`](../../ee/README.md)

## Context
ADR 0004 put Enterprise features in `ee/` under a commercial license, loaded only with a valid
license key, and said that the core never imports from `ee/`. Phase 11 puts the first features
there: retention policies, department budgets, chargeback reports and advanced audit export.
Before any of them is written, the boundary needs to exist in code:

- The Apache-2.0 core must stay complete and must run with no Enterprise code installed.
- Enterprise code must not run without a license, and must stop when the license expires.
- A bug in Enterprise code must not take the product down.
- Licenses must be checkable offline: self-hosted deployments often have no outbound access.
- Developers (and the Phase 11 work) need Enterprise turned on locally without editing `.env`
  and without a key that would also work in production.
- One Docker image should serve both editions.

## Decisions

### 1. A license is an Ed25519-signed token, checked offline
`base64url(payload JSON) "." base64url(Ed25519 signature of those bytes)`. The payload has a
version, the licensee, the features, `issued_at`, `expires_at` and an optional `deployment_id`;
unknown fields are refused. The algorithm is fixed: there is no header that names it, so there is
nothing to downgrade (unlike a JWT's `alg`). Ed25519 keys and signatures are small, verification
is fast, and the `cryptography` library was already installed (through PyJWT).

Anything wrong with the license means Community and a warning in the log, never a failed start
and never the token in the log: missing, malformed, tampered, unknown key, expired, future-dated,
for another deployment. `Edition.has(feature)` reads the clock on every call, so a license that
expires while the process runs switches its features off at that moment.

### 2. Two kinds of trusted key, one of them only in development
`PRODUCTION_PUBLIC_KEYS` (in the code, empty until official licenses are issued) and a
development public key that is trusted only with `APP_ENV=dev`. `nw license dev` creates a
development key pair in `<repo>/.needwise/` (git- and docker-ignored) and signs a license with
it; compose mounts that folder read-only at `/run/needwise`, and the settings default to the
files there. A development license copied to a `demo` or `production` deployment is refused.

### 3. Discovery through an entry point, only with a license
Enterprise code is the `needwise-ee` package (`ee/python`), an optional extra of the API
(`uv sync --extra ee`) and installed in the API image. It declares the entry point group
`needwise.ee`. The core looks the group up only when the license is valid, so without one
`needwise_ee` is never imported, even when installed. A test checks that no core module imports
it (by parsing the source) and that a process which imports every core module never loads it.

### 4. A registry where every extension names its feature
`setup(registry, edition)` registers routers (mounted under `/api/v1` after the core routes,
behind sign-in and `require_feature`), Celery beat tasks (names prefixed `needwise_ee.`) and
implementations of named hooks (`Hook[P, R]`, called with a typed `registry.call`). Each
registration names the licensed feature it belongs to, and the core checks that feature on each
request, task run and hook call. Setup is all or nothing: an exception is logged, everything
registered is dropped, and the process continues as Community.

### 5. 402 "enterprise_feature" for unlicensed requests
`require_feature(name)` answers 401 for anonymous requests first, then a 402 problem document
with the code `enterprise_feature` and a detail that names the feature and points to
`docs/enterprise.md`. The web app's `EnterpriseGate` and `EnterpriseBadge` only explain; the
API enforces.

### 6. One schema for both editions
Tables that Enterprise features use are created by the core's Alembic migrations, with forced
Row-Level Security like every tenant table. Adding or removing a license never runs a migration.

## Consequences
- The core is complete without `ee/`, and the boundary is tested, not just documented.
- The same image runs either edition; the license (a file or an environment variable) decides.
- The check is a contract mechanism, not copy protection: the source is visible, and a modified
  deployment can remove it. That is a license violation, which code cannot prevent.
- No official licenses exist yet. Issuing them needs a signing key kept offline, its public key
  added to `PRODUCTION_PUBLIC_KEYS`, and a release. Revocation before expiry is not possible;
  short license terms are the mitigation.
- Enterprise code shares the process with the core, so a hang or a crash inside a request is not
  isolated; only failures during setup are.
- Enterprise web screens live in `apps/web` behind `EnterpriseGate`; if they ever hold
  significant logic of their own, they move to a commercially licensed folder.

## Alternatives considered
- **JWT (PyJWT with EdDSA):** a header that names the algorithm, more parsing surface, nothing
  gained for a token only NeedWise issues and reads.
- **RSA or HMAC signatures:** RSA keys and signatures are large; HMAC would put the signing
  secret in every deployment.
- **Online activation or a license server:** does not work in air-gapped deployments, and adds a
  service to run. Possible later, in addition to offline checks.
- **A separate Enterprise image:** two images to build, scan and document; a customer would
  have to switch images to try Enterprise.
- **Loading Enterprise code by a configured import path:** any setting could then load any
  module into the API; an entry point is declared by an installed package.
