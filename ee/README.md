# Enterprise Edition (`ee/`)

This directory will hold features for large organizations: per-document permission sync from
source systems, SCIM provisioning, advanced audit export, data-governance policies and premium
connectors. See [docs/ROADMAP.md §5](../docs/ROADMAP.md) for the full free vs. paid split.

Rules that keep the boundary clean:

- Code outside `ee/` never imports from `ee/`. Enterprise features plug in through the same
  extension points anyone could use, and load only when a valid license key is present.
- Security basics are never placed here. Everything needed to run WorkMind safely is in the
  Apache-2.0 core.
- External contributions to `ee/` are not accepted.

It is currently empty. No Enterprise Edition code exists yet.
