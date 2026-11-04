# 0004. Apache-2.0 open core with `ee/`, contributions under DCO

- Status: Accepted
- Date: 2026-11-02

## Context
The goals are wide adoption and recognition first, then an optional commercial edition.
Enterprise legal teams approve permissive licenses easily. A business can later be built on
hosting, enterprise features and support.

## Decision
- Everything outside `ee/` is licensed under Apache-2.0.
- `ee/` holds Enterprise features under a separate commercial license. Core code never imports
  from `ee/`; enterprise features load only with a valid license key.
- Contributions use the Developer Certificate of Origin (`git commit -s`), checked in CI.
  A CLA is not needed: Apache-2.0 already lets the project ship contributed code inside
  commercial offerings. A CLA mainly matters for relicensing to a non-permissive license,
  which is not planned. `ee/` does not accept external contributions.
- Imported libraries must be permissive (MIT/BSD/Apache). AGPL components may only run as
  separate services.

## Consequences
- Maximum adoption; anyone, including competitors, may host the core.
- The commercial moat is the hosted service, enterprise features and expertise, not the license.
- Low friction for contributors.
- Not legal advice: if WorkMind becomes a company, have a lawyer review `ee/LICENSE` and this
  policy.
