# Models and providers: your own keys, a catalog, and what may leave

Each organization (tenant) can bring its own API keys and model servers, keep a catalog of
models with their prices, choose which model does which kind of work, and decide which
documents may be sent to a model outside its infrastructure. Owners and admins manage this
in **Admin → Models**; everything is also in the API (below).

An organization that configures nothing gets exactly what the deployment is configured with
(`LLM_*`, `LOCAL_LLM_*`, ADR 0007), as before this feature existed.

All of it is in the open-source core: bring-your-own keys and local models are community
features (ROADMAP section 5). Nothing here lives in `ee/`.

## The pieces

**The deployment's provider (`system`).** Read-only, built from the server's settings. Its
models are the ones the settings name for a role (`LLM_MODEL`, `LLM_MODEL_<ROLE>`) and, for
Anthropic, every model with a known price (so an organization can pick Claude Opus 5.5 for one
role without the operator configuring it). If `LOCAL_LLM_BASE_URL` and `LOCAL_LLM_MODEL` are set,
a second read-only provider, `system-local`, holds that local model. The API says whether the
deployment has a key, never which one (not even its last characters).

**Your providers.** Two kinds:

| Kind | URL | Key |
|---|---|---|
| `anthropic` | optional; empty means `https://api.anthropic.com` | your Anthropic API key (needed) |
| `openai_compatible` | required: any `/v1/chat/completions` server (OpenAI, gateways, vLLM, Ollama, llama.cpp, LM Studio) | optional (local servers often need none) |

Mark a provider **local** only when its server runs on infrastructure you control. "Local" is
your statement, not something WorkMind can verify; the data policy relies on it. An Anthropic
provider can never be local.

**The catalog.** Models of your providers, each with a label and its prices in US dollars per
million input and output tokens. A catalog id is `<provider id>:<model name>`, for example
`system:claude-sonnet-5-5` or `0196…:meta-llama/Llama-4`. The model name is what is sent to the
provider and cannot change (add the model again under a new name); agents store catalog ids,
so they stay stable.

**Roles.** Callers ask for a role, never a model: `answer` (cited answers), `router` (rewriting
and planning), `agent` (department, research and analytics agents), `generator` (meeting
extraction) and `judge` (evaluations). Each role uses the model you chose for it, or the
deployment's default. An agent definition can name its own catalog model; without one, it uses
the `agent` role ([docs/agents.md](agents.md#model)). If a model you chose for a role is no
longer usable (disabled, or its provider disabled), the role falls back to the deployment's
default rather than failing. An agent's own model does not fall back: saving an agent with a
model the catalog does not offer is refused (`422 unknown_model` or `model_disabled`), and if it
becomes unusable later, that agent's run fails ("model unavailable") rather than going to
another model. Disabling or deleting a model that a role, the data policy or an agent uses is
refused (409), so this only happens when the deployment's own configuration changes.

**Prices and cost.** The cost shown with an answer uses your catalog price for your models,
then the built-in price list (`providers/pricing.py`) by model name. A model with neither shows
its cost as unknown rather than a guess.

## The data policy: what may leave

Documents carry a sensitivity label (`internal`, `confidential`, `restricted`; ARCHITECTURE 7.3).
The policy says the highest label that may be sent to an **external** model (a hosted API, or
any provider not marked local): `public`, `internal`, `confidential` or `restricted`, plus an
optional **local model** for the rest.

When an answer involves sources above the threshold and the answering model is external:

1. with a local model, the whole request goes to the local model instead, sources included;
2. without one, those sources are left out, renumbered away, and the person is told how many
   were withheld and why. If nothing is left, no model is called.

Routing happens after both permission locks, so it never widens what anyone can see. It applies
to single answers, the agents' answers and each department agent's findings.

Defaults keep the behaviour of the older tenant setting `restricted_data` (ADR 0008):
`local_only` (the default) means up to `confidential`; `allow` means everything. The local model
defaults to the deployment's `LOCAL_LLM_*` model if there is one. Once an admin saves a policy,
it replaces both.

A model counts as external for routing when any role's model is external, so a mix of local and
external roles is treated cautiously.

**Not covered yet.** The policy looks at retrieved passages in the answer paths. It does not
filter: results of `search_knowledge` inside research mode and workflows' research steps (the
tool loop sends what the person may read to the `agent` model), query results of the analytics
agent, or meeting transcripts sent for extraction. These go to the model of their role whatever
their labels. Per-department or per-collection policies ("advanced routing policies" in
ROADMAP section 5) are not built.

## Keys: write-only and encrypted

- **Never returned, logged or audited.** Responses carry `has_key` and `key_hint` (`…a1b2`, the
  last four characters, and only for keys of 12 characters or more). Audit events record
  `"key": "set" | "replaced" | "cleared"`, never the key or its hint. Request validation errors
  no longer echo the values of fields named like keys, tokens, secrets or passwords.
- **Sent to one place.** A provider's key is given only to that provider's client, which sends
  it only to that provider's URL and does not follow redirects. Changing a provider's URL needs
  the key again (`422 key_required`), so a key never follows a new URL. Errors a provider returns
  are scrubbed of the key (and of its first and last 8 characters) before they are logged or
  shown. A tenant Anthropic client is built with explicit credentials, so the SDK never falls
  back to the deployment's `ANTHROPIC_API_KEY` or a login profile.
- **Encrypted at rest** with AES-256-GCM under `SECRETS_ENCRYPTION_KEY` (32 random bytes,
  base64). Each stored value is `version | key id | nonce | ciphertext+tag`, where the key id is
  the first 4 bytes of a SHA-256 of the key. The ciphertext is bound to its tenant, provider,
  kind and URL as associated data: copied to another row or tenant, or left behind by a URL
  change made in the database, it does not decrypt. A tampered value fails its integrity check.
- **Required outside development.** With `APP_ENV` other than `dev` or `test`, the API refuses to
  start without `SECRETS_ENCRYPTION_KEY`, or with the development fallback (a fixed, clearly
  marked value used only in `dev` and `test`). It also refuses keys that are not 32 bytes of
  base64. Set it before upgrading a demo or production deployment:

```bash
python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"
```

`SECRETS_ENCRYPTION_KEY` is separate from `SECRETS_KEY`, which still encrypts connector
credentials (docs/connectors.md) and has no key ids.

### Rotating the key

1. Generate a new key. Set it as `SECRETS_ENCRYPTION_KEY` and put the old one in
   `SECRETS_ENCRYPTION_PREVIOUS_KEYS` (comma-separated; listed keys only decrypt).
2. Restart the API and the workers. New and changed keys are now sealed with the new key; old
   ones still open with the old key.
3. Run `wm secrets rotate`. It re-encrypts every stored provider key (and every SQL source
   password, [analytics-sql-sources.md](analytics-sql-sources.md#the-password)) with the active
   key and reports how many it moved, how many were already on it, and how many it could not
   decrypt.
4. When it reports no failures, remove the old key from `SECRETS_ENCRYPTION_PREVIOUS_KEYS` and
   restart. A key that could not be decrypted (its key was lost) must be entered again in
   Admin → Models; until then calls to that provider fail with that message.

## Base URLs and server-side requests

Refused when saved, and checked again when a provider is tested: anything but `http` and
`https`; credentials, a query or a fragment in the URL; cloud metadata endpoints
(`169.254.169.254`, `fd00:ec2::254`, `100.100.100.200`, `metadata.google.internal`, …);
link-local, multicast and unspecified addresses, whether written as an address or reached
through a host name (names are resolved at those two moments).

**Remaining risk.** Private and loopback addresses are allowed on purpose: local model servers
live there. So an org admin can make WorkMind send requests to other hosts on its network. The
provider test reports only a status code and the provider's error code (never a response body),
and answers are only shown to the people asking, but a model call to an internal service still
happens. Names are not re-resolved on every request, so a name that later resolves elsewhere
(DNS rebinding) is not caught. In a deployment where org admins are not trusted with the
internal network (several organizations on one deployment), restrict egress at the network
level.

## How a request finds its model

`resources.models` (an `LLMResolver`) is the one seam every model call goes through: chat and
`/ask`, research mode, the agents' planner and department agents (the test console included),
workflows (scheduled, manual and `document.ready` runs), meeting extraction, the analytics
agent, the MCP server's `ask` and the evaluations.

```python
llm = await resources.models.for_tenant(ctx.tenant_id)  # a TenantLLM, itself an LLMProvider
text, usage = await llm.complete("router", request)      # the tenant's model for the role
agent_llm = llm.select(agent.model)                      # an agent's own model; None if unusable
route(passages, policy=llm.max_external_sensitivity, llm=llm, local_llm=llm.local)
```

Each call's usage is stamped with the catalog model that served it (`usage.catalog_model`) and
the tenant's price (`usage.price_per_mtok`). Resolution is cached per tenant in each process.
After 5 seconds, one small query compares the tenant's settings version (raised by every change
in Admin → Models) and its `restricted_data` setting; a difference reloads. The process that made
a change drops its cache at once; others (more API processes, Celery workers) see it within 5
seconds. Workers build their resources per task, so they always start fresh.

## API

Owners and admins only, with a browser session (access tokens cannot administer). Every change
is audited.

| Method | Path | |
|---|---|---|
| GET | `/api/v1/admin/models` | providers, models, effective roles and policy, and the deployment's `defaults` |
| POST | `/api/v1/admin/models/providers` | `{name, kind, base_url?, api_key?, local?, enabled?}` |
| PATCH | `/api/v1/admin/models/providers/{id}` | any of `name, base_url, api_key, clear_key, local, enabled`; no `api_key` keeps the key |
| DELETE | `/api/v1/admin/models/providers/{id}` | with its models; 409 while one is in use |
| POST | `/api/v1/admin/models/providers/{id}/test` | `{model?}`: one tiny request, 10 s at most → `{ok, latency_ms, error, model}` |
| POST | `/api/v1/admin/models/catalog` | `{provider_id, name, label?, input_usd_per_mtok?, output_usd_per_mtok?, enabled?}` |
| PATCH | `/api/v1/admin/models/catalog/{id}` | `label`, prices (`null` clears one), `enabled` |
| DELETE | `/api/v1/admin/models/catalog/{id}` | 409 while in use |
| PUT | `/api/v1/admin/models/roles` | `{answer, router, judge, generator, agent}`: catalog ids, `null` for the default |
| PUT | `/api/v1/admin/models/policy` | `{max_external_sensitivity, local_model}`; the local model must be on a local provider |

The deployment's providers and models answer `409 read_only` to changes. A provider or model in
use answers `409 model_in_use` and says where ("the judge role", "the agent 'Legal agent'").
Other modules can register their own checks (`model_catalog/references.py`); agent definitions
(`agent_definitions.model`) are checked out of the box.

## What was tested, and what was not

Tested (unit and integration, against PostgreSQL with row-level security, with scripted models;
no real provider was called): encryption round trips, tamper and wrong-context detection, and
rotation; base URL checks, including names that resolve to metadata addresses; that a key
appears in no API response (validation errors included), no audit event and no log line at any
level while it is created, changed, used and tested; two tenants resolving the same role to
their own providers with their own keys; a change reaching a second resolver through the
settings version; 409 for models used by a role, the policy or an agent; an agent's model
checked against the catalog when it is saved (422) and run through the tenant's resolution
(and failing, not falling back, once disabled); `document.ready` workflow runs using the
tenant's role models; the policy end to end
with real retrieval (sources above the threshold go to the local model or are withheld, and
the external model is not called); the provider test's error reporting; tenant isolation.

Not measured: latency added by resolution (one indexed query per tenant every 5 seconds per
process, plus the first load), and any real provider through a tenant key. Not built: budgets
per model (budgets are per organization, department or person: [docs/usage.md](usage.md); the
resolution is where every call is metered and checked against them), a rate limit on the
provider test, streaming in the provider test, and the policy for tool results noted above.

Catalog prices are what usage and budgets count: a model with a price in the catalog is costed
at it, one without falls back to the static table in `providers/pricing.py`, and one with
neither has no cost ([docs/usage.md](usage.md)). The provider test is not recorded in usage.
