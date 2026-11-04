# Security Policy

WorkMind AI handles sensitive company knowledge, so security reports get priority.

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private vulnerability reporting: go to the
repository's **Security** tab and choose **Report a vulnerability**.

Please include the affected version or commit, steps to reproduce, and the impact you expect
(for example: a user can retrieve content from a collection they cannot access).

You can expect an acknowledgment within 3 business days and a status update within 10.
We will credit you in the release notes unless you prefer otherwise.

## Scope

High-priority classes of issues:

- Retrieval or display of content a user is not authorized to see, including through agents,
  tools, citations, caches or error messages
- Cross-tenant data access
- Prompt injection that leads to unauthorized actions or data exfiltration
- Authentication or session weaknesses

## Supported versions

WorkMind is pre-1.0. Only the latest release receives security fixes.
