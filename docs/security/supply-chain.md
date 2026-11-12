# Supply-chain security

What WorkMind checks about the code and images it depends on and ships, where each check runs,
what fails a build, what the first scan found on 2026-11-12, and what is not covered. The threat
is the "Supply chain" row of [ARCHITECTURE §20](../ARCHITECTURE.md#20-security-and-threat-model):
a malicious or vulnerable dependency, base image or build tool.

Every scanner runs from a container image pinned by digest, through one script,
[`scripts/security-scan.sh`](../../scripts/security-scan.sh). CI and a laptop run the same
commands, and nothing gets installed:

```bash
make scan          # lockfiles, repository, Dockerfiles, workflows (needs only Docker)
make scan-images   # builds workmind-api:scan and workmind-web:scan, scans them, removes them
scripts/security-scan.sh image workmind-api:dev report.json   # any local image, full JSON report
scripts/security-scan.sh sbom workmind-web:dev web.spdx.json  # SPDX SBOM of a local image
```

## What runs where

| Check | Tool (pinned) | Where | Fails the build when |
|---|---|---|---|
| Known vulnerabilities in `apps/api/uv.lock` and `apps/web/pnpm-lock.yaml`, dev dependencies included | osv-scanner 2.7.0 | Security workflow, `make scan` | any known vulnerability that [`osv-scanner.toml`](../../osv-scanner.toml) does not except |
| The repository: vulnerabilities in the lockfiles (dev included), misconfigurations (Dockerfiles; Kubernetes and Helm files once they exist), secrets | Trivy 0.76.2 | Security workflow, `make scan` | a fixable CRITICAL or HIGH vulnerability, a CRITICAL or HIGH misconfiguration, a CRITICAL or HIGH secret |
| The api and web images, built from the commit (linux/amd64) | Trivy 0.76.2 | Security workflow, `make scan-images` | a fixable CRITICAL or HIGH vulnerability, a CRITICAL or HIGH secret |
| The release images, both platforms, as pushed | Trivy 0.76.2 | [`release.yml`](../../.github/workflows/release.yml) | the same; a failed scan leaves the images untagged |
| SBOMs (SPDX 2.3 JSON) | syft 1.57.0; BuildKit for releases | Security workflow artifacts (source tree and each image, kept 90 days); release assets and image attestations | never (inventory) |
| Dockerfile lint | hadolint 2.15.1 | Security workflow, `make scan` | any finding, info level included |
| Workflow lint and audit | actionlint 1.7.12 (with shellcheck), zizmor 1.32.0, shellcheck on `scripts/` | Security workflow, `make scan` | any actionlint or shellcheck error; a zizmor finding of medium severity or above |
| Static analysis | CodeQL, `security-extended` (python, javascript-typescript, actions) | [`codeql.yml`](../../.github/workflows/codeql.yml) | results go to Security > Code scanning; they block a merge only if branch protection requires the check |
| Secrets in git history | gitleaks 8.30.1 | `ci.yml`, pre-commit | any leak |
| Updates | Dependabot | weekly, grouped: uv, npm, Dockerfile bases, compose images, actions | (opens PRs) |

The Security workflow ([`security.yml`](../../.github/workflows/security.yml)) runs on pushes to
`main`, on pull requests, weekly (Monday 05:17 UTC, so new advisories against unchanged code are
found) and on demand. Every job has read-only permissions.

## Policy

- **Our dependencies are strict.** We choose the versions in the lockfiles, so any known
  vulnerability fails until it is fixed or excepted in writing.
- **Images gate on what can be fixed.** A fixable CRITICAL or HIGH finding fails. Findings with
  no patched package yet are reported, not gated: there is nothing to upgrade to, and the weekly
  scan shows when a fix lands. The api image runs `apt-get upgrade` and the web image
  `apk upgrade` at build time, so OS fixes apply as soon as the distribution publishes them,
  without waiting for a new base image.
- **Exceptions are written down and expire.** [`.trivyignore.yaml`](../../.trivyignore.yaml)
  entries carry a `statement` and an `expired_at`; `osv-scanner.toml` entries a `reason` and an
  `ignoreUntil`. An expired exception fails the scan again. They are listed
  [below](#exceptions-in-force).
- **Everything is pinned.**
  - Third-party images by digest, with the tag kept for people (`postgres:18.6@sha256:…`):
    compose.yaml, the CI service containers, the Dockerfile base images, the OpenFGA CLI in
    ci.yml and the Makefile, the scanners, BuildKit and binfmt in release.yml.
  - Actions by full commit SHA with a version comment (`# v7.4.0`). GitHub's own `actions/*`
    stay on major tags, as ci.yml has always used them; [`.github/zizmor.yml`](../../.github/zizmor.yml)
    enforces exactly that split.
  - The gitleaks binary that ci.yml downloads is checked against its release SHA-256.
- **Images are minimal.** Non-root numeric users (10001 for api, 1001 for web), no pip, npm,
  corepack or yarn in the runtime images, no secrets in any layer (`.env*` is in both
  `.dockerignore` files, and the image scan includes Trivy's secret scanner).
- **New versions wait a week.** Dependabot proposes a version only after it has been published
  for 7 days (`cooldown`), so a release that is pulled soon after publishing never reaches a PR.
  Security updates are not delayed.

## First scan: 2026-11-12

Measured on the `p12-supply` branch: the lockfiles as committed, both Dockerfiles, and the api and
web images built locally on an Apple Silicon machine (linux/arm64), scanned before and after the
changes below. Scanner versions as in the table above; Trivy's database as of that day.

### Lockfiles

`uv.lock` (121 packages, with the `meetings` and `ee` extras and the dev group) and
`pnpm-lock.yaml` (732 packages):

| Scanner | Findings |
|---|---|
| osv-scanner | 2, both in npm development tools: minimatch 10.2.6 (moderate) and deepmerge 4.3.1 (low). Excepted, see below |
| Trivy, dev dependencies included | 0 fixable CRITICAL or HIGH |
| pip-audit 2.10.0 (cross-check, every extra and group) | 0 |
| pnpm audit (cross-check) | the same 2 as osv-scanner |

No dependency needed a bump for a CRITICAL or HIGH finding, so neither lockfile changed.

### Dockerfiles

| Finding | Where | Outcome |
|---|---|---|
| hadolint DL3066: non-numeric `USER` (Kubernetes `runAsNonRoot` cannot verify a user name) | both | Fixed: `USER 10001:10001` and `USER 1001:1001` |
| hadolint DL3022: `COPY --from=ee` names no earlier stage | api, twice | False positive: `ee` is a named build context. Ignored inline with that reason |
| Trivy DS-0026 (LOW): no `HEALTHCHECK` | api | Accepted: one image runs the API, workers, beat, migrations and seeding; compose gives each service its own health check |
| Base images on floating tags (`python:3.13-slim`, `node:24-alpine`, the uv image) | both | Fixed: pinned by digest (`python:3.13.18-slim-trixie`, `node:24.22.3-alpine3.24`, uv 0.11.17) |

### Images

| | api before | api after | web before | web after |
|---|---|---|---|---|
| Fixable CRITICAL/HIGH | 7 | **0** | 6 | **0** |
| All vulnerabilities | 216 | 205 | 20 | **0** |
| Secret-scanner matches | 11 (false positives) | 11, excepted | 0 | 0 |

What the fixable ones were, and the fix:

- **api, zlib1g (2 HIGH):** Debian had published the fix after the `python:3.13-slim` image was
  built. Fixed by `apt-get upgrade` in the base stage.
- **api, pip's vendored packages (5 HIGH):** urllib3 2.7.0 (2), msgpack 1.1.2 (2) and setuptools
  70.3.0 (1), inside the base image's pip. The app runs from a virtualenv that uv builds and has
  no use for pip, so the runtime image no longer has it.
- **web, npm's own dependencies (6 HIGH):** undici 6.28.0 (4) and brace-expansion 5.0.9 (2) in the
  npm that ships with the Node image, plus 13 MEDIUM and 1 LOW findings in the same tree. The
  standalone Next.js server needs only `node`, so npm, corepack and yarn are removed from the
  runtime image. With `apk upgrade`, the web image has no known vulnerability at any severity.

The hardened images were then run as a whole stack (a separate compose project, started from
those images with `--no-build`): migrations, the API, worker and beat (as 10001:10001, beat
writing its schedule to its volume), the web app behind Caddy, seeding the Acme corpus through
ingestion, and the retrieval permission gate with 0 leaks. The full backend test suite (1115
tests) and the frontend gate (lint, types, 1023 tests, build) pass on the branch.

What stays in the api image: 205 findings, none with a fix available, all in Debian 13 packages
(47 HIGH, among them util-linux, sqlite, ncurses, systemd libraries, perl-base and acl). None is
in a Python package. They clear when Debian ships fixes; the weekly scan reports it.

The 11 secret-scanner matches are the `gcp-service-account` rule firing on docstrings of the
Anthropic SDK's managed-agents beta, which show an example JSON object with
`"type": "service_account"` and no key material. The exception is scoped to that package's
directory, so the rule still applies to every other file in the image.

### Third-party images (reported, not gated)

We do not build these, so their findings do not fail our builds. Scanned from the registry at the
pinned digests, linux/amd64:

| Image | Role | CRITICAL / HIGH | Fixable CRITICAL/HIGH, and where |
|---|---|---|---|
| `postgres:18.6` | database | 2 / 147 | 28: zlib1g (2), Go standard library 1.24.6 in `gosu` (26) |
| `valkey/valkey:9.1.3-alpine` | cache, queues | 0 / 0 | 0 |
| `qdrant/qdrant:v1.20.0` | vector store | 0 / 56 | 10: apt (2), zlib1g (2), follow-redirects and undici in its bundled Node.js (6) |
| `openfga/openfga:v1.22.1` | authorization | 1 / 9 | 10: Go standard library, golang.org/x/net, go-jose, ca-certificates |
| `quay.io/keycloak/keycloak:26.8.1` | login | 0 / 6 | 1: guava |
| `caddy:2.11.7-alpine` | entry point | 1 / 10 | 10: brotli, curl, Go standard library, golang.org/x/net, go-jose |
| `axllent/mailpit:v1.27` | development mail catcher | 3 / 75 | 78: OpenSSL, musl, zlib, Go standard library and modules (mailpit 1.27.11) |
| `ghcr.io/ggml-org/llama.cpp:server-b12098` | optional embeddings (profile) | 0 / 2 | 2: apt |

Newer releases exist for some (caddy 2.11.9, mailpit 1.33, keycloak 26.8.2 published today).
They are not bumped here: the stack and its measurements were made with these versions, and an
infrastructure upgrade belongs in its own PR where the CI stack job tests it. Dependabot's
`compose-images` group proposes them weekly. Rerun one with
`scripts/security-scan.sh remote <image@digest> linux/amd64`.

## Exceptions in force

| ID | File | Why it is acceptable | Expires |
|---|---|---|---|
| GHSA-g3hv-75wg-m249 (CVE-2026-118764), minimatch 10.2.6, moderate | `osv-scanner.toml` | Pattern amplification when an attacker controls the glob pattern. Development only: typescript-eslint and shadcn's ts-morph use it with patterns from our own config; not in the web image. The fix is minimatch 11, which `@typescript-eslint/typescript-estree` 8.72 does not accept (`^10.2.2`) | 2027-02-12 |
| GHSA-gcqx-w48m-w2h4 (CVE-2026-93753), deepmerge 4.3.1, low | `osv-scanner.toml` | Prototype poisoning of merged objects. Development only: the shadcn CLI merges its own registry metadata; not in the web image. No fixed version exists | 2027-02-12 |
| `gcp-service-account` secret rule, scoped to `/app/.venv/lib/python*/site-packages/anthropic/**` | `.trivyignore.yaml` | False positive in SDK docstrings (above) | 2027-05-12 |

To add one: put it in the file with a reason and an expiry, add a row here, and say in the PR why
fixing it is not possible now.

## Updating the scanners

Dependabot does not update the images run with `docker run` in `scripts/security-scan.sh`, nor the
BuildKit and binfmt images in `release.yml`, nor the gitleaks version and checksum in `ci.yml`.
Check them about monthly: pick a release at least a week old, resolve its multi-arch digest, and
change tag and digest together.

```bash
docker buildx imagetools inspect aquasec/trivy:0.76.2   # "Digest:" is the index digest to pin
```

Trivy downloads its vulnerability database and osv-scanner queries osv.dev on every run, so an
older scanner still sees new advisories; updates bring new detectors and fewer false positives.

## Not covered

- **Pull requests scan linux/amd64 images only.** arm64 can differ (other wheels, other OS
  packages); the release workflow scans both platforms before anything gets a version tag.
- **Third-party images are not gated**, only reported here (see above).
- **No cosign signatures.** Release images get BuildKit SBOM and provenance attestations, and
  GitHub-signed build provenance (Sigstore) when the repository is public. GitHub artifact
  attestations are not available to private repositories without GitHub Enterprise Cloud.
- **No license compliance check.** The SBOMs list licenses; nothing evaluates them yet.
- **Images are not bit-for-bit reproducible.** Release builds set `SOURCE_DATE_EPOCH` from the
  tagged commit, but `apt-get upgrade` and `apk upgrade` take whatever the distribution publishes
  that day.
- **CodeQL and the Security workflow are advisory until branch protection requires them.** The
  GitHub organization and branch protection are not set up yet ([ROADMAP §8](../ROADMAP.md#8-checklist-before-going-public)).
- **ci.yml's checkouts keep the (read-only) token in `.git/config`.** zizmor reports this at low
  severity; the new workflows set `persist-credentials: false`.
- **Not yet run on GitHub.** The workflows pass actionlint, zizmor and the GitHub workflow schema,
  and every scan command was run locally, but no repository exists to run them in yet.
