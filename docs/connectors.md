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
- **failures** are recorded on the source (status, last error), never "half applied", and retried
  at the next interval;
- **one sync per source at a time**, however many workers run.

The `beat` service queues due syncs every minute; workers run them. Admins can also sync now.

## Built-in connectors

| Kind | Configuration | Credentials (stored encrypted) | Deletions |
|---|---|---|---|
| `web` | `start_urls`, `max_pages`, `path_prefix` | none (respects `robots.txt`) | yes, when the crawl finished within `max_pages` |
| `github` | `repo` (`owner/name`), `branch`, `paths` | `token` (optional for public repos) | yes, unless GitHub truncates the tree |
| `slack` | `channels` (ids), `days` of history | `bot_token` (`xoxb-…`: `channels:history`, `channels:read`) | edits and deletions within the window; one document per channel per month |
| `gdrive` | `folder_id` (subfolders included) | OAuth (`drive.readonly`); connect from Admin | yes |

Google Docs, Sheets and Slides are exported as DOCX, XLSX and PPTX. Files WorkMind can't parse
(images, archives) are skipped.

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

## Writing a connector

Implement `scan(cursor) -> ScanResult` and `fetch(item) -> bytes` (`modules/connectors/base.py`).
Add a configuration model in `registry.py`, and add your connector to the contract tests in
`tests/test_connectors_contract.py`. Every connector runs the same checks there against a fake of
its API: stable ids, versions that change exactly with content, parseable files, and failures
raised as `ConnectorError`.
