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
- **deleted** items are removed from WorkMind and from search. This happens after a *complete*
  scan (a scan cut short by a limit, an incremental API or an outage never deletes), or when the
  source itself reports the deletion (a SharePoint delta tombstone);
- **missing upstream**: when a scan that set out to list everything was cut short (the crawler
  reached `max_pages`, GitHub truncated a very large tree), the known items it did not list are
  kept and flagged "missing upstream" from that scan on. The next complete scan deletes them if
  they are really gone; a scan that lists them again clears the flag. Slack only reads a window
  of history and SharePoint's delta feed only reports changes, so neither ever flags anything;
- **failures** are recorded on the source and in its run history, never "half applied", and
  retried at the next interval. When a download fails, the source's cursor stays where it was,
  so an incremental source (a delta feed) reports the item again instead of skipping it;
- **one sync per source at a time**, however many workers run.

The `beat` service queues due syncs every minute; workers run them. People who manage a source
can also choose **Sync now**. Each sync is a **run**: when it started and finished, whether it was
scheduled or someone chose Sync now (and who), what it added, updated, left unchanged, deleted
and flagged as missing, whether the scan was complete, and each error. The newest 100 runs of
each source are kept (`source_runs`).

Each synced document keeps a link back to the original (`url`) and where it sits in its source
(`source_meta`: a Confluence page's space and ancestors, a SharePoint file's folder path). When
only that place changes (a page moved under another parent), the metadata is updated and nothing
is re-indexed. It also records when it last **changed in the source**: the source's own
modification time when the connector has one (Drive's `modifiedTime`, a web page's
`Last-Modified`, the last message or edit of a Slack month, a Confluence page version's date,
SharePoint's `lastModifiedDateTime`), else the time a sync first saw the new content. GitHub has
no per-file date in the tree it reads, so it uses the sync's time.

## Built-in connectors

| Kind | Configuration | Credentials (stored encrypted) | Deletions |
|---|---|---|---|
| `web` | `start_urls`, `max_pages`, `path_prefix` | none (respects `robots.txt`) | yes, when the crawl finished within `max_pages`; otherwise unlisted pages are flagged missing upstream |
| `github` | `repo` (`owner/name`), `branch`, `paths` | `token` (optional for public repos) | yes, unless GitHub truncates the tree (then flagged missing upstream) |
| `slack` | `channels` (ids), `days` of history | **Connect Slack** (OAuth), or a pasted `bot_token` (`xoxb-…`: `channels:history`, `channels:read`) | edits and deletions within the window; one document per channel per month |
| `gdrive` | `folder_id` (subfolders included) | **Connect Google Drive** (OAuth, `drive.readonly`) | yes |
| `confluence` | `site_url`, `spaces` (keys), `include_attachments` | **Connect Confluence** (OAuth 2.0 3LO), or `email` + `api_token` | yes: every scan lists every current page |
| `sharepoint` | `site_url`, `library`, `folder` (all optional) | **Connect SharePoint** (Microsoft identity platform, PKCE) | yes: delta tombstones, and full listings when needed |

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

`GET /api/v1/admin/sources/kinds` describes every kind for a form: its label, how it signs in
(`none`, `token`, `oauth`, `oauth_or_token`), the credential fields one types, the OAuth provider
with its start path, redirect URI and whether the operator configured it, and the JSON schema of
its configuration.

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

## Confluence Cloud

The pages of one or more spaces of a Confluence Cloud site (`https://<name>.atlassian.net`),
through the REST API v2, and the files attached to them that WorkMind can parse (PDF, Word,
Excel, PowerPoint, Markdown, text, HTML, CSV).

- **One collection per source.** All configured spaces land in the source's collection; for
  "HR space → HR collection, Engineering space → Engineering collection", create one source per
  space.
- **What a scan does.** It lists every current page of the spaces with its version number, but
  no bodies, and the attachments of each page (one extra request per page; turn
  `include_attachments` off to skip them). Only pages and attachments whose version changed are
  downloaded. Deleted, archived and draft pages are not listed, so they leave WorkMind; a page
  moved to a space outside the source leaves too.
- **What a page becomes.** A Markdown document: the title, then the body converted from the
  storage format (headings one level below the title, paragraphs, lists, tables as rows, code
  blocks, task lists, link texts). Macro parameters, images and emoticons are dropped; macros
  that wrap content (info, note, panel, expand) keep it.
- **The page tree** is the document's source metadata (`space_key`, `space_name`, `parent_id`,
  `ancestors`, `path`). Folders, whiteboards and databases are not read: a page under a folder
  has the space as its first ancestor.
- **Links back** open the page (or the attachment's preview) on the site.
- **A space key WorkMind cannot see is a sync error**, never an empty listing that would delete
  everything synced from it.

### Option A: an API token

The simplest setup for one site: a service account's email and an API token.

1. Sign in to <https://id.atlassian.com/manage-profile/security/api-tokens> as the account
   WorkMind should read with, and create an API token. That account needs view access to the
   spaces (and to restricted pages, if those should be synced).
2. Create the source with the credentials:

```bash
curl -X POST http://localhost:8480/api/v1/admin/sources -H 'X-CSRF-Protection: 1' ... \
  -d '{"key": "wiki-hr", "name": "HR wiki", "kind": "confluence", "collection": "hr",
       "config": {"site_url": "https://acme.atlassian.net", "spaces": ["HR"]},
       "credentials": {"email": "wiki-sync@acme.example", "api_token": "ATATT..."}}'
```

WorkMind calls `https://acme.atlassian.net/wiki/...` with Basic authentication.

### Option B: OAuth 2.0 (3LO)

1. In the Atlassian developer console (<https://developer.atlassian.com/console/myapps/>),
   create an **OAuth 2.0 integration**.
2. **Permissions → Confluence API**, add these granular scopes (and nothing else):
   `read:space:confluence`, `read:page:confluence`, `read:attachment:confluence`. WorkMind also
   asks for `offline_access`, which gives a refresh token.
3. **Authorization → OAuth 2.0 (3LO)**, callback URL: `<PUBLIC_URL>/api/v1/oauth/atlassian/callback`.
4. Set `ATLASSIAN_CLIENT_ID` and `ATLASSIAN_CLIENT_SECRET` (the app's client id and secret).
5. Create a `confluence` source without credentials, then open
   `/api/v1/admin/sources/<key>/oauth/atlassian/start` in the browser (a Connect button in the
   UI). Grant access **to the same site** as the source's `site_url`: WorkMind looks the site up
   among the sites the person granted (`accessible-resources`) and stores its cloud id with the
   tokens. A grant for another site is refused (`?oauth=failed`).

WorkMind then calls `https://api.atlassian.com/ex/confluence/<cloud id>/wiki/...`, renews the
access token with the refresh token, and stores the new refresh token each time (Atlassian
rotates them). The connected person's own Confluence permissions decide what is read.

## SharePoint and OneDrive

The files of one document library, or of one folder in it, through Microsoft Graph:

| `site_url` | `library` | What is synced |
|---|---|---|
| `https://contoso.sharepoint.com/sites/finance` | empty | the site's default library ("Documents") |
| `https://contoso.sharepoint.com/sites/finance` | `Policies` (name or drive id) | that library of the site |
| empty | a drive id | that drive (anyone's OneDrive the account can read) |
| empty | empty | the OneDrive of the account that connected |

`folder` (for example `Policies/2026`) narrows any of them to one folder and its subfolders.

- **One collection per source** ("site/library/folder → collection"); create several sources for
  several mappings.
- **Incremental sync with delta queries.** The first sync enumerates the drive
  (`/drives/{id}/root/delta`, following `@odata.nextLink`) and stores the `@odata.deltaLink` in
  the source's cursor. Later syncs ask only for changes since then. A deleted file arrives as a
  tombstone and is removed from WorkMind; a file moved out of the selected folder, or renamed to
  a type WorkMind cannot read, is removed too.
- **Why the connector keeps the folder tree.** Graph supports delta only on a drive's root in
  SharePoint and OneDrive for Business, and its delta items carry no paths. The connector keeps
  the drive's folder ids, parents and names in the cursor to know what is inside the selected
  folder and to record each file's folder path (`source_meta.path`). A file keeps the path it
  had when it last changed: renaming a folder does not touch the files' metadata.
- **When it lists everything again.** Graph reports a moved or deleted folder, but not the files
  inside it. So when a folder inside the selection is deleted, or a folder moves into or out of
  the selection, the connector enumerates the drive again; that listing is complete, so files no
  longer in the selection are deleted. The same happens when Graph answers 410 (the delta token
  expired) or when the source's library or folder changed. A full listing downloads nothing that
  did not change (`cTag`, the content tag, is the version marker).
- **Throttling.** A 429 (or 503 with `Retry-After`) is waited out for as long as Graph asks, up to
  60 seconds per wait and 4 attempts; a longer wait ends the sync with an error, and the next one
  continues from the stored delta link.
- **Downloads** follow Graph's redirect to a pre-authenticated URL; the access token is not sent
  there.
- **Links back** are the files' `webUrl`.

### What to register in Microsoft Entra ID

1. **App registrations → New registration**, supported account type "Accounts in this
   organizational directory only" (single tenant) unless several tenants will connect.
2. **Authentication → Add a platform → Web**, redirect URI:
   `<PUBLIC_URL>/api/v1/oauth/microsoft/callback`.
3. **Certificates & secrets → New client secret**.
4. **API permissions → Microsoft Graph → Delegated**: WorkMind asks, per source, only for the one
   permission it needs, plus `offline_access` (a refresh token):
   - `Sites.Read.All` for a SharePoint site's library (`site_url` set);
   - `Files.Read.All` for a drive given by id;
   - `Files.Read` for the connecting account's own OneDrive.
   Add those your sources use. None of them requires admin consent by default, but tenants that
   restrict user consent need an admin to grant them.
5. Set `MICROSOFT_CLIENT_ID` (the application id), `MICROSOFT_CLIENT_SECRET` and
   `MICROSOFT_TENANT_ID` (the directory id or a verified domain; `organizations`, the default,
   accepts any work or school account).
6. Create a `sharepoint` source, then open `/api/v1/admin/sources/<key>/oauth/microsoft/start`
   in the browser and sign in. The flow is the authorization code grant with PKCE; WorkMind
   stores the refresh token encrypted and the new one Microsoft returns at each renewal.

The connected account's own SharePoint permissions decide what is read: sign in with an account
that can read the library (a dedicated service account is easiest to reason about).

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
| `GET /admin/sources/kinds` | Every connector: label, sign-in, credential fields, OAuth provider and configuration schema |
| `POST /admin/sources` | Create (optional `stale_after_days`) |
| `PUT /admin/sources/{key}` | Name, configuration, schedule, enabled, `stale_after_days` (left out: unchanged) |
| `POST /admin/sources/{key}/pause`, `/resume` | Pause or resume (audited) |
| `PUT /admin/sources/{key}/credentials` | Replace (or with `{}` remove) the credentials; write-only |
| `POST /admin/sources/{key}/sync` | Sync now (rate limited: `sync`; `409 source_paused` while paused) |
| `GET /admin/sources/{key}/runs?limit=` | The run history, newest first, errors with `plain` explanations |
| `GET /admin/sources/stale-documents?source=&days=` | Stale synced documents (titles and links withheld where the caller cannot open the collection) |
| `DELETE /admin/sources/{key}` | Disconnect: the source, its runs and its documents |
| `GET /admin/sources/{key}/oauth/{google,slack,atlassian,microsoft}/start` | Start Connect (`?back=knowledge` returns to Knowledge Hub › Sources) |
| `GET /admin/sources/stale?days=` | Org admins only: every document (uploads too) past `valid_until` or not updated in WorkMind |

## What has and has not been tested

- **Web** (against a live site) and **GitHub** (against a public repository) were run live.
- **Slack**, **Google Drive**, **Confluence** and **SharePoint/OneDrive** have not been run
  against a live workspace, site or tenant: there are no test accounts for them in this project.
  They are tested against fakes of their HTTP APIs built from the official documentation
  (`tests/test_connectors_contract.py`; for Confluence and SharePoint the fuller fakes in
  `tests/fake_confluence.py` and `tests/fake_graph.py`, exercised by
  `tests/test_connectors_confluence.py`, `tests/test_connectors_sharepoint.py` and, through the
  sync engine with PostgreSQL and Qdrant, `tests/integration/test_sync.py`). A fake shows that
  WorkMind handles the documented behaviour; it cannot show that the service behaves as
  documented. Points worth checking first on a live tenant: the attachment download through
  OAuth (the v1 download endpoint with the granular scope `read:attachment:confluence`), and
  whether SharePoint reports the files of a deleted folder one by one (the connector does not
  rely on it).
- The OAuth flows (Google, Slack, Atlassian, Microsoft) were tested up to the token endpoints,
  answered as documented.

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

- Confluence sources accept only `https://<name>.atlassian.net` sites and SharePoint sources only
  `https://<name>.sharepoint.com` sites. Paging and delta links are followed only on the
  service's own host, and download redirects go out without credentials (https only, through the
  outbound URL policy).
- OAuth flows are stored under the tenant's key prefix for 10 minutes and used once; a callback
  in another person's or tenant's session fails.

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
- Notifications are in-app only (no email or Slack), and there is no escalation when an owner
  ignores a stale-document task.
- The run history keeps the newest 100 runs per source; older ones are only in the audit log
  (`source.synced`).
- Live verification: Connect Slack, Connect Google Drive, Confluence and SharePoint/OneDrive were
  tested against scripted providers and fakes of their documented APIs, not against a real Slack
  workspace, Google account, Confluence site or Microsoft 365 tenant.

## Writing a connector

Implement `scan(cursor) -> ScanResult` and `fetch(item) -> bytes` (`modules/connectors/base.py`).

- Return `complete=True` only when the scan saw everything, and `truncated=True` when it set out
  to and was cut short (that turns unlisted items into "missing upstream"); report explicit
  deletions in `ScanResult.deleted`; keep what the next scan needs (a delta link) in
  `ScanResult.cursor`.
- Put a stable id, a version that changes exactly with the content, a link back (`url`), the
  item's place (`meta`) and, when the source has one, its modification time (`modified_at`) on
  each `RemoteItem`.
- Raise `ConnectorError` for anything the source refuses or fails; use
  `modules/connectors/transport.py` for HTTP (throttling, errors) and `RefreshingToken` in
  `oauth.py` for OAuth access tokens (set `updated_credentials` so renewed tokens are stored).
- Add a configuration model and a `KindInfo` entry in `registry.py` (label, auth kind, credential
  fields, OAuth provider), the kind to `KINDS` in `models.py` (a migration widens the check
  constraint), and your connector to the contract tests in `tests/test_connectors_contract.py`.
  Every connector runs the same checks there against a fake of its API: stable ids, versions that
  change exactly with content, parseable files, and failures raised as `ConnectorError`. Plain
  explanations of its error messages go in `modules/connectors/health.py`.

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
