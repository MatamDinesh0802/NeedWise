# Security Policy

WorkMind AI handles sensitive company knowledge, so security reports get priority.

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private vulnerability reporting: go to the
repository's **Security** tab and choose **Report a vulnerability**.

Please include the affected version or commit, steps to reproduce, and the impact you expect
(for example: a user can retrieve content from a collection they cannot access).

You can expect an acknowledgment within 3 business days and a status update within 10.
We will credit you in the release notes unless you prefer otherwise.

## Supported versions

| Version | Security fixes |
|---|---|
| 1.x, latest minor release | Yes: a patch release (1.y.z) |
| 1.x, older minor releases | No: upgrade to the latest 1.x minor. Within 1.x, minor releases are backwards compatible and migrations only go forward (Alembic), so the upgrade is the fix |
| 0.x (pre-1.0) | No: upgrade to 1.x (the release notes have the upgrade path, [ROADMAP section 6](docs/ROADMAP.md#6-release-process)) |

A fix is announced in the release notes and, for high-priority issues, in a GitHub security
advisory. Enterprise Edition code (`ee/`) follows the same policy; Enterprise customers also get
the support terms of their agreement.

## Scope

High-priority classes of issues:

- Retrieval or display of content a user is not authorized to see, through any surface: chat
  answers and citations, agents mode, configured agents, research mode, the MCP server, tools,
  approvals, workflows, meetings, the analytics (BI) agent and its SQL, caches, run event
  streams or error messages
- Cross-tenant data access
- Prompt injection that leads to unauthorized actions or data exfiltration: from documents,
  synced Drive, Slack, GitHub or web content, meeting transcripts, uploaded CSV/XLSX data,
  remote MCP tool results or workflow events. A model *saying* something wrong because a
  document told it to is a known limitation (see the threat model); a model *doing* or
  *revealing* something beyond the asking person's access is a vulnerability
- Model output that a client renders unsafely: script execution, images or requests that leave
  the deployment, links that hide where they go
- Server-side request forgery through model provider base URLs, connectors or tools
- Excessive agency: a tool with side effects that runs without an approval, or as someone other
  than the person who asked; a guest reaching tools or data meant for members
- Unbounded consumption that bypasses budgets or the per-person limits
- Exposure of secrets: provider API keys, connector credentials, session ids, access tokens
- Authentication or session weaknesses, CSRF
- Tampering with the audit log or usage records by the application role

Out of scope: denial of service by volume against your own deployment; findings that need an
org admin or the operator to act against their own organization (they are trusted for
configuration; see "Malicious insider admin" in the threat model); vulnerabilities in
Keycloak, PostgreSQL, Qdrant, Valkey or OpenFGA themselves (report those upstream); social
engineering.

## Threat model

[docs/security/threat-model.md](docs/security/threat-model.md) describes the assets, actors,
trust boundaries and the known residual risks. [docs/security/owasp-llm-top10.md](docs/security/owasp-llm-top10.md)
maps the controls to the OWASP Top 10 for LLM Applications (2025), with the tests that prove
them. The red-team suite (`evals/datasets/red_team.jsonl`) runs its retrieval checks on every
push; a report that comes with a new attack for it is especially welcome.
