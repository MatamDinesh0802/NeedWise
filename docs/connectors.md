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
- **failures** are recorded on the source (status, last error), never "half applied", and retried
  at the next interval. When a download fails, the source's cursor stays where it was, so an
  incremental source (a delta feed) reports the item again instead of skipping it;
- **one sync per source at a time**, however many workers run.

Each synced document keeps a link back to the original (`url`) and where it sits in its source
(`source_meta`: a Confluence page's space and ancestors, a SharePoint file's folder path). When
only that place changes (a page moved under another parent), the metadata is updated and nothing
is re-indexed.

The `beat` service queues due syncs every minute; workers run them. Admins can also sync now.

## Built-in connectors

| Kind | Configuration | Credentials (stored encrypted) | Deletions |
|---|---|---|---|
| `web` | `start_urls`, `max_pages`, `path_prefix` | none (respects `robots.txt`) | yes, when the crawl finished within `max_pages` |
| `github` | `repo` (`owner/name`), `branch`, `paths` | `token` (optional for public repos) | yes, unless GitHub truncates the tree |
| `slack` | `channels` (ids), `days` of history | `bot_token` (`xoxb-…`: `channels:history`, `channels:read`) | edits and deletions within the window; one document per channel per month |
| `gdrive` | `folder_id` (subfolders included) | OAuth (`drive.readonly`); connect from Admin | yes |
| `confluence` | `site_url`, `spaces` (keys), `include_attachments` | OAuth 2.0 (3LO), or `email` + `api_token` | yes: every scan lists every current page |
| `sharepoint` | `site_url`, `library`, `folder` (all optional) | OAuth (Microsoft identity platform, PKCE); connect from Admin | yes: delta tombstones, and full listings when needed |

Google Docs, Sheets and Slides are exported as DOCX, XLSX and PPTX. Files WorkMind can't parse
(images, archives) are skipped.

`GET /api/v1/admin/sources/kinds` describes every kind for an admin UI: its label, how it signs
in (`none`, `token`, `oauth`, `oauth_or_token`), the credential fields an admin types, the OAuth
provider with its start path, redirect URI and whether the operator configured it, and the JSON
schema of its configuration.

## Setting one up

```bash
# A public website or wiki section
curl -X POST http://localhost:8480/api/v1/admin/sources -H 'X-CSRF-Protection: 1' ... \
  -d '{"key": "handbook-site", "name": "Handbook site", "kind": "web",
       "collection": "company-handbook", "config": {"start_urls": ["https://intranet.example/handbook/"]}}'
```

Or use **Admin → Sources** in the app.

**Google Drive**
1. Create an OAuth client ("Web application") in Google Cloud.
2. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, and add the redirect URI
   `<PUBLIC_URL>/api/v1/oauth/google/callback`.
3. Create a `gdrive` source with the folder id, then choose **Connect Google Drive**.
   WorkMind stores the refresh token encrypted and renews access tokens itself.

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
- The OAuth flows were tested up to the token endpoints, answered as documented.

## Security

- Credentials are encrypted with AES-256-GCM (`SECRETS_KEY`) and bound to their source: a
  ciphertext copied onto another source does not decrypt. The API never returns them.
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
- Confluence sources accept only `https://<name>.atlassian.net` sites and SharePoint sources only
  `https://<name>.sharepoint.com` sites. Paging and delta links are followed only on the
  service's own host, and download redirects go out without credentials (https only, through the
  outbound URL policy).
- OAuth flows are stored under the tenant's key prefix for 10 minutes and used once; a callback
  in another person's or tenant's session fails.

## Writing a connector

Implement `scan(cursor) -> ScanResult` and `fetch(item) -> bytes` (`modules/connectors/base.py`).

- Return `complete=True` only when the scan saw everything; report explicit deletions in
  `ScanResult.deleted`; keep what the next scan needs (a delta link) in `ScanResult.cursor`.
- Put a stable id, a version that changes exactly with the content, a link back (`url`) and the
  item's place (`meta`) on each `RemoteItem`.
- Raise `ConnectorError` for anything the source refuses or fails; use
  `modules/connectors/transport.py` for HTTP (throttling, errors) and `RefreshingToken` in
  `oauth.py` for OAuth access tokens (set `updated_credentials` so renewed tokens are stored).
- Add a configuration model and a `KindInfo` entry in `registry.py` (label, auth kind, credential
  fields, OAuth provider), the kind to `KINDS` in `models.py` (a migration widens the check
  constraint), and your connector to the contract tests in `tests/test_connectors_contract.py`.
  Every connector runs the same checks there against a fake of its API: stable ids, versions that
  change exactly with content, parseable files, and failures raised as `ConnectorError`.
