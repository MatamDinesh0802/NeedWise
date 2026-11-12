# Releasing WorkMind

How a version gets from `main` to a GitHub Release with images on GHCR, what a release
contains, and how to cut v1.0.0. The policy is [ROADMAP §6](../ROADMAP.md#6-release-process);
the checks the images pass are in [docs/security/supply-chain.md](../security/supply-chain.md).

> **Status:** not run on GitHub yet, because there is no repository to run it in
> ([ROADMAP §8](../ROADMAP.md#8-checklist-before-going-public)). Checked so far: the workflows
> pass actionlint, zizmor and the GitHub workflow schema; release-please 17.6.0's own updaters,
> run on the repository's files, bump all five version entries and leave `uv lock --check`
> passing; and the `permission-gate` job's commands, run locally against images built from this
> branch (their own compose project and tags, started with `--no-build` through
> `WORKMIND_API_IMAGE` and `WORKMIND_WEB_IMAGE`), gave a healthy stack, the seeded Acme corpus
> and 0 leaks in 48 retrieval checks. Building for arm64 under QEMU, pushing to GHCR, the
> attestations and the release upload are untested.

## Versions

SemVer. Before 1.0, a minor version is a phase (0.1, 0.2…) and patch versions are fixes. One
version for the whole repository, kept in:

- `apps/api/pyproject.toml` and `ee/python/pyproject.toml`, and their two entries in
  `apps/api/uv.lock` (otherwise `uv sync --locked` fails after a bump),
- `apps/web/package.json`,
- `.release-please-manifest.json` (the last released version).

release-please changes all of them together ([`release-please-config.json`](../../release-please-config.json)).
The API reports its version in `GET /api/v1/system/status` and in the OpenAPI document.

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

## Cutting v1.0.0

The only tag so far is `v0.1.0` (Phase 1). Phases 2 to 11 were finished on `main` but never
tagged or published, so `.release-please-manifest.json` says `0.1.0` and the package files still
say `0.0.1`. Left alone, the first release PR would propose 0.2.0. For v1.0.0:

1. Before the release, run the full evaluation with real models (`uv run wm eval --judge`, and
   the suites in [evals/README.md](../../evals/README.md)) on the release candidate and commit
   the reports to `evals/results/`, so `eval-reports-v1.0.0.tar.gz` has numbers for this version.
2. Push a commit to `main` with a `Release-As` footer:

   ```bash
   git commit --allow-empty -s -m "chore: release 1.0.0" -m "Release-As: 1.0.0"
   ```

   release-please then proposes 1.0.0 and bumps all the version files from 0.0.1.
3. In the release PR, replace the hand-written "Unreleased (Phase 12)" section of `CHANGELOG.md`
   with the generated entry (release-please inserts its entry below that section, above v0.11),
   keeping what the generated list lacks: upgrade notes and the measured numbers.
4. Close and reopen the PR so CI runs, then merge.

`bootstrap-sha` in `release-please-config.json` points at the last Phase 11 commit, so the first
generated entry lists Phase 12's commits only. Once v1.0.0 exists, release-please finds the last
release from its tag and `bootstrap-sha` is no longer used.

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
- No secrets: every workflow uses the built-in `GITHUB_TOKEN`.
