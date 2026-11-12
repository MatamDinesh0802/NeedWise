# WorkMind AI

WorkMind is an open-source AI workspace for companies that you run on your own servers.
Employees ask questions in plain language and get answers from company documents, with a
citation for every claim, drawn only from sources they are allowed to read. Department agents,
meeting notes, questions about company data and approved actions build on the same
permission checks.

!!! warning "Status: not ready for real company data"
    Phases 0 to 11 of the [roadmap](ROADMAP.md) are built. Phase 12, production hardening for
    v1.0, is in progress. Until v1.0, run WorkMind on the fictional demo company or on test
    data only.

## Who it is for

- **Teams and companies** that want an assistant over their own documents, where an engineer
  cannot read salaries through the assistant just because the assistant can.
- **IT and platform teams** who must keep data on their own infrastructure: WorkMind runs with
  Docker Compose, and every model can be local.
- **Developers** who want a permission-aware knowledge source inside Claude Code, Cursor or
  Claude Desktop, over MCP.

## What it does

| Capability | In short |
|---|---|
| **Cited answers** | Streaming answers from the documents you may read, each claim marked with its source; "I couldn't find this" when the sources don't say. [Assistant](user-guide/assistant.md) |
| **Permissions in code** | Search filters every passage by its access list, then re-checks each source with OpenFGA. The model only sees what the asking person may read. [Two locks](adr/0006-two-lock-permission-aware-retrieval.md) |
| **Knowledge Hub** | Upload PDF, Word, Excel, PowerPoint, Markdown, HTML, CSV or Slack exports, or sync a website, GitHub, Slack or Google Drive. [Knowledge](user-guide/knowledge.md), [Connectors](connectors.md) |
| **Agents as configuration** | Department agents an administrator adds in the browser, with a test console and versions. [Agents](agents.md) |
| **Insights** | Questions about company tables answered with a chart, the SQL and checked numbers, under row-level policies. [Insights](analytics.md) |
| **Meetings** | A recording or transcript becomes decisions and action items that link to the moment they were said. [Meetings](meetings.md) |
| **Approvals** | Anything that changes something outside WorkMind waits for a person to approve it. [Approvals and tasks](user-guide/approvals-and-tasks.md) |
| **Your models** | Claude by default, or any OpenAI-compatible server, per organization and per role; restricted documents can be kept on a local model. [Models](models.md), [Local models](getting-started/local-models.md) |
| **Measured** | `wm eval` asks the same questions as different employees and must find zero permission leaks. [Evaluation](evals/README.md) |

## Quickstart

Requirements: Docker with Compose v2.

```bash
git clone <repo-url> workmind && cd workmind
make quickstart
```

Then open http://localhost:8480 and sign in as one of the demo users. Search works right away;
answers need a model. [Quickstart](getting-started/quickstart.md) has the demo logins and the
next steps.

## Where to read next

| You want to | Read |
|---|---|
| Try it on the demo company | [Quickstart](getting-started/quickstart.md) |
| Use the assistant, upload documents, approve actions | [User guide](user-guide/assistant.md) |
| Add agents, models, budgets, connectors | [Admin guide](agents.md) |
| Configure a deployment | [Configuration](getting-started/configuration.md) and the [reference](reference/configuration.md) |
| Call the API | [HTTP API](reference/api.md) |
| Run it on Kubernetes, back it up, upgrade it | [Kubernetes (Helm)](deploy/kubernetes.md), [backup and restore](operations/backup-restore.md), [upgrades](operations/upgrade.md), [multi-tenant mode](multi-tenancy.md) |
| Check how it is secured | [Threat model](security/threat-model.md), [OWASP Top 10 for LLM Applications review](security/owasp-llm-top10.md), [supply chain](security/supply-chain.md) |
| Understand the design | [Architecture](ARCHITECTURE.md) and the [decision records](adr/README.md) |
| See what was measured | [Evaluation](evals/README.md) and [load testing](load-testing.md) |
| See what comes next | [Roadmap](ROADMAP.md) |

## License

The core is under the Apache License 2.0. The `ee/` directory (Enterprise features, which need
a license key) is under a separate commercial license: [Enterprise edition](enterprise.md). All
demo data is fictional.
