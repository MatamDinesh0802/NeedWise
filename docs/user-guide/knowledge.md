# Knowledge and uploads

Everything the assistant knows lives in **collections**, shown in the **Knowledge Hub**
(`/knowledge`). A collection is like a shared folder: its access rules decide who can read its
documents, and they apply everywhere, in search, answers, agents, meetings and MCP.

## Collections and who sees them

| Scope | Who can read it by default | Acme example |
|---|---|---|
| Personal | Only its owner | My notes |
| Department | The department's members | Engineering Docs |
| Enterprise | Everyone in the organization except guests | Company Handbook |
| Restricted | Only the people and groups it is shared with | Employee Records, Payroll |

- **Viewers** can read a collection's documents and find them in search.
- **Editors** can also upload, re-index and delete documents, and change their labels.
- **Owners and admins create collections and share them** in **Admin › Collections**. Being an
  organization admin does not, by itself, let you read a restricted collection: someone has to
  share it with you.

The Knowledge Hub lists only the collections you can read. A collection you can't read does not
appear, and opening its link says it doesn't exist or isn't shared with you.

## Upload documents

Open a collection you can edit, then drop files on **Upload documents** (or choose them). Each
file is uploaded, then indexed in the background; the table shows its status (*pending*,
*processing*, *ready* or *failed*) and updates by itself.

| Formats | |
|---|---|
| Documents | PDF, Word (`.docx`), PowerPoint (`.pptx`), Markdown, plain text, HTML |
| Tables | Excel (`.xlsx`), CSV |
| Chat | Slack exports (`.json`) |

- **Size:** up to 50 MB per file by default (`MAX_UPLOAD_BYTES`).
- **New versions:** uploading a file with the same name to the same collection adds a version;
  uploading identical bytes again changes nothing. Only documents you can see count: a document
  whose own access list leaves you out, or one synced from a source, is never updated by your
  upload (your file becomes a new document of its own).
- **Scanned PDFs** have no text layer, and NeedWise does not run OCR: they fail with "no
  readable text found (a scanned PDF needs OCR)".
- **In a browser only.** Uploads need a signed-in browser session; personal access tokens
  cannot upload. To pull documents in automatically, use a [connector](../connectors.md)
  (a website, GitHub, Slack or Google Drive).

Meeting recordings and transcripts are uploaded on the **Meetings** page instead, which turns
them into notes as well as a searchable transcript ([Meetings](../meetings.md)).

## What indexing does

1. **Parse**: the text is read with its headings and page numbers, which citations show later.
2. **Chunk**: the text is cut by section into passages of up to about 320 tokens; a table is
   split by rows, with its header repeated in every piece.
3. **Label**: a scan suggests a sensitivity label from what the text contains: a valid IBAN or a
   US social security number makes it *restricted*, a pay word next to an amount makes it
   *confidential*. Words alone ("medical", "salary") never raise it, and the scan never lowers
   the collection's default. It is a pattern scan, not named-entity detection: names, home
   addresses, phone numbers and email addresses do not raise the label, so a collection that
   holds personal data should get the right default (or an editor sets the label).
4. **Embed and index**: each passage is stored for hybrid search (meaning and keywords), with the
   collection's access list attached, so a search can only ever return what the person may read.
   A document can also have **its own access list**, narrower than its collection's: synced from
   its source (Enterprise permission sync) or set by a collection editor on an upload
   (`PUT /api/v1/documents/{id}/acl`). Only people on it who can read the collection see the
   document; for anyone else it does not exist ([permission-sync.md](../permission-sync.md)).

When a new version is ready, the previous version's passages are removed; the document is never
missing from search in between. Workflows can start when a document is ready
([Automation](../automation.md#event-triggers)).

## A document's page

Click a document to see:

- its **status**, **trust level** and **sensitivity label**, its versions, and the number of
  chunks;
- **the chunks the assistant reads**, with a search box (**Find in chunks**), so you can check
  what a question could match;
- **Download original** and, for editors, **Re-index** and **Delete document** (the document,
  all its versions and its search entries; this can't be undone);
- **Ownership and review**: who answers for the document, when it was last reviewed, and whether
  answers treat it as official ([Knowledge governance](../governance.md)).

## Trust levels and sensitivity labels

Two labels change how a document is used:

| Trust level | Meaning | Effect on answers |
|---|---|---|
| `official` | Approved by the collection's owner (or an admin) | Ranked higher; preferred when sources disagree |
| `reference` | Ordinary documentation | Normal ranking |
| `discussion` | Chats, tickets, meeting transcripts | The answer says when it relies on one |

| Sensitivity | Effect |
|---|---|
| `internal` | The default |
| `confidential` | Business sensitive |
| `restricted` | Personal data, salaries, legal: by default, never sent to an external AI model |

Editors can change a document's sensitivity and set it to `reference` or `discussion`; the
change applies to search at once and is kept when the document is re-indexed. **Official status
needs an approval**: an editor requests it and the collection's owner approves it in
**Approvals** (an org owner or admin who can open the collection decides when the owner is the
one asking, or there is none). Nobody approves their own request, and a new version drops back
to the collection's default until it is approved again
([Knowledge governance](../governance.md#official-status)).

Which labels may reach an external model is the organization's data policy
([Models and keys](../models.md#the-data-policy-what-may-leave)).

## Documents synced from other tools

Some collections are kept up to date by a **source**: a website, a GitHub repository, Slack
channels or a Google Drive folder. A synced document changes when it changes in its source, and
disappears from NeedWise when it is deleted there. If you own a collection, **Knowledge Hub ›
Sources** shows the sources that sync into it: whether they are healthy, when they last synced,
what went wrong, and which documents look stale. If you own a synced document that has not
changed in its source for a long time, or that the source no longer lists, you get a task once
to check it ([Connectors](../connectors.md#stale-documents-and-reminders)).
