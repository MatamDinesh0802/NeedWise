# Releasing WorkMind

How a version gets from `main` to a GitHub Release with images on GHCR, what a release
contains, and how v1.0.0 was cut and is published. The policy is [ROADMAP §6](../ROADMAP.md#6-release-process);
the checks the images pass are in [docs/security/supply-chain.md](../security/supply-chain.md).

> **Status:** not run on GitHub yet, because there is no repository to run it in
> ([ROADMAP §8](../ROADMAP.md#8-checklist-before-going-public)); v1.0.0 was tagged by hand
> ([below](#v100)). Checked so far: the workflows pass actionlint, zizmor and the GitHub workflow
> schema; release-please 17.6.0's own updaters, run on the repository's files on 2026-11-13, bump
> all nine version entries (the chart's `appVersion` through its marker comment: release-please's
> YAML updater rewrites the whole file and drops the comments, so it is not used); the release
> workflow's version check, run locally, passes for 1.0.0 and names every file for another
> version; and the `permission-gate` job's commands, run locally against images built from this
> branch (their own compose project and tags, started with `--no-build` through
> `WORKMIND_API_IMAGE` and `WORKMIND_WEB_IMAGE`), gave a healthy stack, the seeded Acme corpus
> and 0 leaks in 48 retrieval checks. Building for arm64 under QEMU, pushing to GHCR, the
> attestations and the release upload are untested.

## Versions

SemVer. Before 1.0, a minor version is a phase (0.1, 0.2…) and patch versions are fixes. One
version for the whole repository, kept in:

- `apps/api/pyproject.toml` and `ee/python/pyproject.toml`, and their two entries in
  `apps/api/uv.lock` (otherwise `uv sync --locked` fails after a bump); both packages read their
  `__version__` from this metadata,
- `apps/web/package.json` and `e2e/browser/package.json`,
- `loadtests/pyproject.toml` and its entry in `loadtests/uv.lock`,
- `appVersion` in `deploy/helm/workmind/Chart.yaml` (the image tag the chart deploys by default;
  the chart's own `version` is bumped by hand whenever the chart changes),
- `.release-please-manifest.json` (the last released version).

release-please changes all of them together ([`release-please-config.json`](../../release-please-config.json)).
The release workflow's `prepare` job refuses a tag whose version the API, Enterprise and web
packages or the chart's `appVersion` do not carry. The API reports its version in `GET /api/v1/system/status` and in the OpenAPI document.

## The flow

1. **Commits on `main` follow Conventional Commits.** `feat:` and `fix:` decide the next version;
   `perf:`, `eval:`, `build:`, `ci:` and `docs:` also appear in the changelog; `refactor:`,
   `test:` and `chore:` do not. A breaking change is `feat!:` or a `BREAKING CHANGE:` footer; before
   1.0 it bumps the minor version (`bump-minor-pre-major`).
2. **release-please keeps a release PR open** ([`release-please.yml`](../../.github/workflows/release-please.yml),
   on every push to `main`): "chore: release X.Y.Z", with the version bumps and a new
   `CHANGELOG.md` entry. Edit that entry in the PR to add upgrade notes and to call out breaking
   changes and migrations.
   - The PR is opened with the workflow's own token, and GitHub starts no workflows for events
     that token causes. **To run CI on the release PR, close it and reopen it** (or push a commit
     to its branch).
3. **Merging the release PR releases.** release-please tags `vX.Y.Z` on the merge commit and
   creates the GitHub Release with the changelog entry. Its `publish` job then runs
   [`release.yml`](../../.github/workflows/release.yml) for that tag:

   | Job | What it does | Permissions |
   |---|---|---|
   | `prepare` | Checks the tag is `vX.Y.Z` or `vX.Y.Z-pre` and that the tagged source carries that version | read |
   | `images` (api, web in parallel) | Builds linux/amd64 and linux/arm64 (arm64 under QEMU), with BuildKit SBOM and provenance (`mode=max`) attestations; pushes **by digest only**; adds GitHub-signed build provenance when the repository is public; scans both platforms with Trivy (fixable CRITICAL/HIGH or a secret fails); extracts the SBOMs | packages, attestations, id-token |
   | `permission-gate` | Runs the compose stack **from those digests** (`WORKMIND_API_IMAGE`, `WORKMIND_WEB_IMAGE`), seeds Acme Robotics and runs the retrieval permission gate: every red-team attack, every retrieval mode, with hash embeddings and no model calls. A leak fails the release | packages: read |
   | `publish-images` | Only now tags the same image indexes `X.Y.Z` and `X.Y` (no `X.Y` for a pre-release, never `latest`) | packages |
   | `github-release` | Attaches the SBOMs, the permission-gate report and the eval reports; adds the image digests and verification commands to the release notes | contents |

   If a scan or the permission gate fails, nothing gets a version tag: the pushed digests stay
   untagged in GHCR, and the GitHub Release (already created by release-please) has no assets.
   Fix forward with a new patch release, or delete the release and the tag.
4. **Then, by hand** (ROADMAP §6): the LinkedIn post, a devlog entry in `docs/devlog/`, and a
   refreshed demo deployment.

## What a release contains

| Asset | Where | What it is |
|---|---|---|
| `ghcr.io/<owner>/workmind-api:X.Y.Z` | GHCR | API, workers, beat, migrations, seeding; includes the Enterprise package, inactive without a license. linux/amd64 and linux/arm64 |
| `ghcr.io/<owner>/workmind-web:X.Y.Z` | GHCR | The Next.js app. linux/amd64 and linux/arm64 |
| SBOM and provenance attestations | inside each image index | BuildKit's SPDX SBOM and SLSA provenance per platform: `docker buildx imagetools inspect <image> --format '{{ json .SBOM }}'` (or `.Provenance`) |
| GitHub build provenance | GitHub attestations, public repository only | Sigstore-signed: `gh attestation verify oci://ghcr.io/<owner>/workmind-api:X.Y.Z --owner <owner>` |
| `workmind-{api,web}-X.Y.Z-linux-{amd64,arm64}.spdx.json` | release assets | The same SBOMs as files |
| `permission-gate-vX.Y.Z.md` | release asset | The permission gate run by `release.yml` on the released images. Leaks must be 0; recall numbers in it come from hash embeddings and say nothing about answer quality |
| `eval-reports-vX.Y.Z.tar.gz` | release asset | `evals/README.md` and the committed reports in `evals/results/` as of the tag: the quality, latency and cost runs made with real models (CI has no model keys, so it cannot make them) |
| Changelog and upgrade notes | release notes, `CHANGELOG.md` | Written by release-please, edited in the release PR |

## Running a release

```bash
# .env (see .env.example)
WORKMIND_API_IMAGE=ghcr.io/<owner>/workmind-api:1.0.0
WORKMIND_WEB_IMAGE=ghcr.io/<owner>/workmind-web:1.0.0
```

```bash
docker compose pull && docker compose up -d --no-build --wait
```

`make up` builds from the checkout and would overwrite those tags with a local build, so use the
commands above. In production, pin the digest from the release notes as well
(`…/workmind-api:1.0.0@sha256:…`). The `migrate` service runs `alembic upgrade head` before the
API starts: migrations only go forward, and the release notes call out breaking ones. Take a
backup before upgrading.

## v1.0.0

v1.0.0 was cut by hand on 2026-11-13, without release-please, because there is no GitHub
repository to run it in. Before it, the only tag was `v0.1.0` (Phase 1); Phases 2 to 11 were
finished on `main` without tags.

1. **The release-candidate evaluation** ran on `144afd2` with Claude Sonnet 5.5 and its reports
   are in `evals/results/` (`2026-11-13-v1.0.0-*`). The model provider's credit ran out during
   the third run, so only the permission gate, the core suites and the request-routing rules
   completed; the other suites' latest numbers are the full v1.0 benchmark of 2026-11-12
   ([v1.0 benchmarks](../benchmarks/v1.0.md#v100-release-check-2026-11-13)).
2. **Versions** were set to 1.0.0 by hand in every file listed [above](#versions), the chart to
   0.2.0, and the manifest to 1.0.0; `uv lock` updated the two lockfiles.
3. **The changelog entry and the release notes** were written by hand: the v1.0.0 section of
   [`CHANGELOG.md`](../../CHANGELOG.md) and [docs/releases/v1.0.0.md](../releases/v1.0.0.md).
4. **The tag:** an annotated `v1.0.0` on the release commit, in the maintainer's repository only.

### Publishing v1.0.0 once the repository exists

1. Do the [repository settings](#repository-settings-this-needs) first.
2. Push `main` and the tag: `git push origin main v1.0.0`. The pushed tag runs
   [`release.yml`](../../.github/workflows/release.yml) directly ([below](#other-ways-to-start-a-release)):
   `prepare` checks the versions against the tag, then the images are built, scanned, put
   through the permission gate and tagged `1.0.0` and `1.0`, and `github-release` creates the
   GitHub Release with generated notes, since none exists. Replace those notes with
   [docs/releases/v1.0.0.md](../releases/v1.0.0.md).
3. The release attaches `evals/README.md` and the committed reports as of the tag, so its
   `eval-reports-v1.0.0.tar.gz` holds the release check and the full v1.0 benchmark.
4. From then on release-please takes over: it reads 1.0.0 from the manifest, finds that release
   (from the GitHub Release, or the tag), and the next release PR lists only the commits after
   it. Not tested; if its first PR lists older commits, set `last-release-sha` in
   `release-please-config.json` to the tagged commit. `bootstrap-sha` is no longer used once a
   release is found.

## Other ways to start a release

- **A tag pushed by hand** (`git tag -s v1.0.1 && git push origin v1.0.1`) runs `release.yml`
  directly. The versions in the tagged files must already match the tag (`prepare` checks), and if
  no GitHub Release exists for the tag, the workflow creates one with generated notes.
- **Again, for an existing tag**: Actions > Release > Run workflow, with the tag. Use it when a run
  failed before `publish-images`, for example on a registry outage. After `publish-images` has run,
  publishing again would move `X.Y.Z` to a newly built digest (the OS packages can differ by
  build day): release a new patch version instead.

## Repository settings this needs

Set once, when the GitHub repository exists:

- Settings > Actions > General > Workflow permissions: allow GitHub Actions to create and approve
  pull requests (release-please opens its PR with the workflow token).
- Packages `workmind-api` and `workmind-web` (created by the first release): make them public and
  link them to the repository, so anyone can pull them.
- A ruleset that protects `v*` tags, and branch protection on `main` that requires the CI,
  Security and CodeQL checks.
- Settings > General > "Require contributors to sign off on web-based commits", so commits made
  in the web editor (the changelog edit in step 3) carry the `Signed-off-by:` line the DCO job
  checks.
- No secrets for building, testing and releasing: those workflows use the built-in
  `GITHUB_TOKEN`. The optional load test against a deployment (`loadtest.yml`) reads the
  repository secret `DEMO_PASSWORD`.
