# Connectors: keeping WorkMind in sync with your tools

A **source** pulls documents from somewhere else into **one collection**. Everything synced from
that source gets the collection's access rules: "this Drive folder → Finance" is one line of
configuration. Per-document permission sync, mirroring each file's ACL in the source, is an
enterprise feature (`ee/`, planned).

## How a sync works

Every connector does two things: **scan** (list items with a version marker) and **fetch** (get
one item's bytes). The sync engine does the rest the same way for all of them:

- **new or changed** items become a new document version and are indexed;
- **unchanged** items (same version marker) are skipped without being downloaded;
- **deleted** items are removed from WorkMind and from search. This only happens after a
  *complete* scan, so a scan cut short by a limit, an incremental API or an outage never deletes;
- **missing upstream**: when a scan that set out to list everything was cut short (the crawler
  reached `max_pages`, GitHub truncated a very large tree), the known items it did not list are
  kept and flagged "missing upstream" from that scan on. The next complete scan deletes them if
  they are really gone; a scan that lists them again clears the flag. Slack only reads a window
  of history, so older months are never suspects;
- **failures** are recorded on the source and in its run history, never "half applied", and
  retried at the next interval;
- **one sync per source at a time**, however many workers run.

The `beat` service queues due syncs every minute; workers run them. People who manage a source
can also choose **Sync now**. Each sync is a **run**: when it started and finished, whether it was
scheduled or someone chose Sync now (and who), what it added, updated, left unchanged, deleted
and flagged as missing, whether the scan was complete, and each error. The newest 100 runs of
each source are kept (`source_runs`).

Each synced document also records when it last **changed in the source**: the source's own
modification time when the connector has one (Drive's `modifiedTime`, a web page's
`Last-Modified`, the last message or edit of a Slack month), else the time a sync first saw the
new content. GitHub has no per-file date in the tree it reads, so it uses the sync's time.

## Built-in connectors

| Kind | Configuration | Credentials (stored encrypted) | Deletions |
|---|---|---|---|
| `web` | `start_urls`, `max_pages`, `path_prefix` | none (respects `robots.txt`) | yes, when the crawl finished within `max_pages`; otherwise unlisted pages are flagged missing upstream |
| `github` | `repo` (`owner/name`), `branch`, `paths` | `token` (optional for public repos) | yes, unless GitHub truncates the tree (then flagged missing upstream) |
| `slack` | `channels` (ids), `days` of history | **Connect Slack** (OAuth), or a pasted `bot_token` (`xoxb-…`: `channels:history`, `channels:read`) | edits and deletions within the window; one document per channel per month |
| `gdrive` | `folder_id` (subfolders included) | **Connect Google Drive** (OAuth, `drive.readonly`) | yes |

Google Docs, Sheets and Slides are exported as DOCX, XLSX and PPTX. Files WorkMind can't parse
(images, archives) are skipped.

## Who manages sources

Checked by the API on every request (`api/v1/sources.py`), never by a model. Managing sources
needs a browser session: an access token gets `403 scope_required`.

| Who | What |
|---|---|
| Org owners and admins | Every source, in **Admin › Sources** (and in Knowledge Hub › Sources) |
| The owner of a collection | The sources that sync into the collections they own, in **Knowledge Hub › Sources**; they can add sources to those collections only |
| Anyone else | Nothing: `403 forbidden` (the page says "You don't own a collection") |

A source outside the caller's collections answers `404`, and creating a source in a collection
the caller does not own answers `422 unknown_collection`, the same as a collection that does not
exist. The person who creates a source is its owner: syncs run on their behalf (the audit actor,
the documents' uploader, so by default their owner, docs/governance.md).

## The Sources page

Admin › Sources (`/admin/sources`) and Knowledge Hub › Sources (`/knowledge/sources`, linked from
the Knowledge Hub for collection owners) show the same page; the API decides which sources are on
it. Described in words, since the docs carry no screenshots:

- **At the top**, a **New source** button and a note that synced documents get their
  collection's access rules and that a source failing 3 syncs in a row (the configured threshold)
  notifies the people who manage it. Below it, a row of filter toggles with counts: **All**,
  **Failing**, **Never synced**, **Paused**, **Healthy**. Failing sources are listed first.
- **One card per source**: its name and key, the kind, the number of documents, the schedule and
  who set it up; a health badge (and a "Syncing…" badge while a sync runs). A **Mapping** box
  reads like "acme/eng-handbook (main) → Engineering Docs → visible to Marcus Bell (owner) ·
  Engineering (department) can view", with the collection linked and, for admins, a link to change
  who can see it (Admin › Collections). Then **Last successful sync**, **Next scheduled sync**
  ("In 30 min", "Due now", "Paused") and **Last run** ("30 minutes ago: 2 added · 1 updated · 11
  unchanged · 1 deleted").
- **Errors** of a failing source sit in a red box: the explanation in plain words first ("What
  this source points at wasn't found: check the repository, folder, channel or start URL, and
  that the account can see it."), then "Failed 3 times in a row · the people who manage it were
  notified", and the source's own message behind **What the source said**.
- A link such as **3 stale documents (1 missing upstream, 2 unchanged past the threshold)** opens
  that source's stale documents.
- **Actions** on each card: **Connect Google Drive** / **Connect Slack** (or Reconnect),
  **Credentials** (write-only: you can replace or remove them, never read them), **History**,
  **Disconnect**, **Edit**, **Pause** / **Resume**, **Sync now** (disabled while paused or
  syncing).
- **History** opens a side panel with two tabs. **Run history**: newest first, each run with a
  Succeeded/Failed/Running badge, when, "Scheduled" or "Sync now by Erin Takahashi", how long it
  took, Added/Updated/Deleted counts, a note when the scan did not list everything (so nothing was
  deleted), and each error with the item it concerns, its plain explanation and the raw message.
  **Stale documents**: that source's stale documents.
- **Edit** changes what the source syncs (start URLs and page limit, repository, branch and
  paths, channels and days, Drive folder), its name, the schedule (every 15 minutes to every
  week), whether it is enabled, and **Stale after** (never, 30 days, 3 months, 6 months, a year,
  2 years). The collection it syncs into cannot be changed (see "What is not built").
- **Disconnect** asks you to type the source's key. It removes the source, its credentials and
  its run history, and deletes every document it synced, from the knowledge base and from search.
  Nothing changes in the source itself. To only stop syncing, **Pause** it: its documents stay
  searchable.
- **Stale synced documents**, below the cards: every stale document of the sources on the page,
  with the reason ("Not listed by the source since Nov 11, 2026", "Unchanged in the source since
  Feb 8, 2026"), the source, the collection, the owner, when the owner was reminded, a link to the
  document and **Open in the source**. A select switches between each source's own threshold and
  3 months, 6 months or a year. An admin who cannot open a collection sees "A document in “HR
  Guidance (internal)” you can't open", without title or link (separation of duties, like the
  governance overview).

Loading shows skeletons, a failed request an error with **Retry**, and a page without sources an
empty state. Notification links (`/admin/sources?source=<key>`, `/knowledge/sources?source=<key>`)
open that source's history.

### Health

| Health | Means |
|---|---|
| Healthy | The last finished sync succeeded |
| Failing | The last finished sync failed (counted: failures in a row) |
| Paused | Disabled: no scheduled syncs and no Sync now; documents stay searchable |
| Never synced | No sync has succeeded yet, and none has failed |

Paused wins over the others. While a sync runs, the source keeps the health of the previous one.

## Setting one up

Choose **New source**, pick the kind, name it, choose the collection (admins: any; collection
owners: one they own) and fill in what it syncs. Web and GitHub sources sync right away; Drive and
Slack sources go to the provider's consent screen right after saving (a Slack source can take a
pasted bot token instead). Through the API:

```bash
# A public website or wiki section
curl -X POST http://localhost:8480/api/v1/admin/sources -H 'X-CSRF-Protection: 1' ... \
  -d '{"key": "handbook-site", "name": "Handbook site", "kind": "web",
       "collection": "company-handbook", "config": {"start_urls": ["https://intranet.example/handbook/"]}}'
```

**Google Drive**
1. Create an OAuth client ("Web application") in Google Cloud.
2. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, and add the redirect URI
   `<PUBLIC_URL>/api/v1/oauth/google/callback`.
3. Create a `gdrive` source with the folder id, then choose **Connect Google Drive**.
   WorkMind stores the refresh token encrypted and renews access tokens itself.

**Slack**
1. Create a Slack app with the bot scopes `channels:history`, `channels:read`,
   `groups:history` and `groups:read` (the last two for private channels it is invited to), and
   the redirect URL `<PUBLIC_URL>/api/v1/oauth/slack/callback`.
2. Set `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET` (Helm: `config.slack.clientId` and
   `credentials.slackClientSecret`).
3. Create a `slack` source with the channel ids, then choose **Connect Slack**. WorkMind stores
   the bot token Slack grants, encrypted, like a pasted one. Invite the app to each channel.

Without `SLACK_CLIENT_ID`, paste a bot token instead (in the form, or later under
**Credentials**). The consent flows are single use, expire after 10 minutes, are bound to the
person and tenant that started them, and come back to the page they started from.

## Stale documents and reminders

A synced document is **stale** when it is

- **missing upstream**: a scan cut short no longer lists it (always counted, whatever the
  threshold), or
- **not updated**: unchanged in the source for longer than the source's **Stale after**
  (`stale_after_days`: 180 days by default; none for Slack, whose months are an archive; set it
  per source, or to never).

The Celery beat task `workmind.send_source_reminders` runs once a day ("once a day" counts from
when beat started, like the review reminders). For every document that became stale, its owner
(the governance owner, docs/governance.md) gets, **once per staleness episode**:

- one task per owner and run, "Check 3 synced documents that look stale" (or "Check “<title>”: it
  looks stale"), due in 14 days, listing each document with why it looks stale and a link (the
  task page shows them as links; at most 50 are named, the rest counted);
- one notification for that task;
- an audit event per document, `knowledge.document.stale_reminded`.

The episode ends when the document changes in the source (a new version) or is no longer stale
(it is listed again, or the threshold changed); the next time it becomes stale is a new episode.
The marker (`documents.stale_reminded_at`) is written in the same transaction as the task, with
the rows locked, so running the task twice, or twice at once, reminds once. An owner who left, or
who can no longer open the collection, is not reminded (their titles would leak); the document
stays on the Sources page.

On the first daily run after an upgrade, documents synced before it start from their current
version's date, so owners of documents unchanged for longer than the threshold get one task.

## Sources that keep failing

A source counts its failed syncs in a row (`consecutive_failures`, back to 0 after a success).
When it reaches `SOURCE_FAILURE_ALERT_AFTER` (default 3), the people who manage it get a
notification, **once per failure episode**: "“Engineering docs” keeps failing to sync: 3 syncs in
a row failed", with the plain explanation of the last error, linking to the source. Who:

- the source's owner, when they can still manage it (an org admin, or the collection's owner);
- the collection's owner;
- when neither is active, every active org owner and admin.

The first success after an alert tells the same people "“Engineering docs” syncs again" and ends
the episode. The sync engine checks after every sync; the daily task checks again (for example
after the threshold was lowered). A paused source is not alerted. Audited as `source.failing` and
`source.recovered`.

## API

All under `/api/v1/admin/sources`, for org admins and collection owners as described above.

| Method and path | What |
|---|---|
| `GET /admin/sources` | The sources the caller manages, each with health, `next_sync_at`, `last_run`, `last_error_plain`, `consecutive_failures`, stale counts, owner and `access` (the permission mapping as names) |
| `GET /admin/sources/options` | Kinds, whether each one's OAuth is configured, and the collections the caller may sync into |
| `POST /admin/sources` | Create (optional `stale_after_days`) |
| `PUT /admin/sources/{key}` | Name, configuration, schedule, enabled, `stale_after_days` (left out: unchanged) |
| `POST /admin/sources/{key}/pause`, `/resume` | Pause or resume (audited) |
| `PUT /admin/sources/{key}/credentials` | Replace (or with `{}` remove) the credentials; write-only |
| `POST /admin/sources/{key}/sync` | Sync now (rate limited: `sync`; `409 source_paused` while paused) |
| `GET /admin/sources/{key}/runs?limit=` | The run history, newest first, errors with `plain` explanations |
| `GET /admin/sources/stale-documents?source=&days=` | Stale synced documents (titles and links withheld where the caller cannot open the collection) |
| `DELETE /admin/sources/{key}` | Disconnect: the source, its runs and its documents |
| `GET /admin/sources/{key}/oauth/google/start`, `/oauth/slack/start` | Start Connect (`?back=knowledge` returns to Knowledge Hub › Sources) |
| `GET /admin/sources/stale?days=` | Org admins only: every document (uploads too) past `valid_until` or not updated in WorkMind |

## Security

- Credentials are encrypted with AES-256-GCM (`SECRETS_KEY`) and bound to their source: a
  ciphertext copied onto another source does not decrypt. The API never returns them; the page
  only says whether they are stored.
- Production refuses to start with the development `SECRETS_KEY`.
- Synced content is untrusted like any document: it is parsed, chunked and shown to models as
  data, never as instructions.
- Slack messages are always stored with trust `discussion`, whatever the collection's default:
  anyone in a synced channel writes them, so they never become official policy. Other
  connectors take the collection's default trust.
- The web crawler checks every host against the outbound URL policy (`core/urls.py`: no cloud
  metadata, link-local or reserved addresses, also after DNS) before its first request, and
  follows redirects itself, only within the site: a page that redirects elsewhere is skipped,
  a start URL that does is a sync error. Private and loopback addresses are allowed
  (intranets). Start URLs are checked when the source is saved.
- Run histories, stale lists and reminders never carry document content. Error messages are what
  the source answered (status codes, item ids, API error names); they are shown to the people who
  manage the source only.
- `source_runs` is a tenant table with forced row-level security; the new routes are in the
  cross-tenant suite.

### Events in the audit log

| Action | When |
|---|---|
| `admin.source.created`, `.updated`, `.paused`, `.resumed`, `.credentials_changed`, `.connected`, `.deleted` | Someone managed a source |
| `source.synced` | A sync finished (status and counts) |
| `source.failing` | A source reached the failure threshold (who was notified) |
| `source.recovered` | The first success after an alert |
| `knowledge.document.stale_reminded` | The daily task reminded a document's owner (reason, task) |

## What is not built

- **Changing the collection a source syncs into.** The mapping's source side (folder, channels,
  repository, URLs) and the collection's access are editable; moving a source's documents to
  another collection is not (it would move their permissions, trust and review cycle). Add a new
  source and disconnect the old one.
- Confluence and SharePoint/OneDrive connectors; per-document permission sync (Enterprise,
  planned).
- Notifications are in-app only (no email or Slack), and there is no escalation when an owner
  ignores a stale-document task.
- The run history keeps the newest 100 runs per source; older ones are only in the audit log
  (`source.synced`).
- Live verification: Connect Slack and Connect Google Drive were tested against scripted
  providers (their documented token endpoints), not against a real Slack workspace or Google
  account.

## Writing a connector

Implement `scan(cursor) -> ScanResult` and `fetch(item) -> bytes` (`modules/connectors/base.py`).
Set `ScanResult.complete` only when the scan listed everything, and `truncated` when it set out to
and was cut short (that turns unlisted items into "missing upstream"); give `RemoteItem.modified_at`
when the source has a modification time. Add a configuration model in `registry.py`, and add your
connector to the contract tests in `tests/test_connectors_contract.py`. Every connector runs the
same checks there against a fake of its API: stable ids, versions that change exactly with
content, parseable files, and failures raised as `ConnectorError`. Plain explanations of its error
messages go in `modules/connectors/health.py`.

## Tests

- `tests/integration/test_sync.py`: runs with their trigger, counts and errors; an interrupted
  run closed by the next claim; failure alerts once per episode, to the source's owner and the
  collection's owner with the right links, recovery, a new episode; missing upstream after a
  truncated scan, cleared when listed again, ignored for a windowed scan, deleted by a complete
  scan; the staleness clock from the source's own date; stale reminders once per episode, a new
  episode after a change, missing-upstream documents without a threshold, no reminder for an owner
  who cannot open the collection; the daily task alerting a failing source.
- `tests/integration/test_sources_api.py`: health, last run and the permission mapping after a
  real crawl; a failing source's plain error; pause and resume; collection owners limited to
  their collections (404 elsewhere, 403 for someone who owns none, 403 with an access token);
  `stale_after_days` defaults and updates; stale documents with titles withheld from an admin who
  cannot open the collection; Connect Slack against a scripted Slack (state bound to the session,
  single use, token stored and never returned).
- `tests/test_connectors_health.py`: health, next sync, the plain explanations, and the
  connectors' truncation and modification times.
- `tests/integration/test_cross_tenant.py` classifies every new route.
- Web: `src/lib/admin/sources.test.ts`, `src/components/admin/sources-section.test.tsx`,
  `src/components/admin/source-detail.test.tsx`, `src/lib/work/tasks.test.ts`.

The page was also checked in a headless Chromium against mocked API answers (the dev server with
every `/api` request answered by the test script): the admin and collection-owner views, the
history panel, the Disconnect dialog and the New source form, with axe reporting no violation in
them. It was not checked against the running stack.
