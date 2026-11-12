# Assistant and citations

The **Assistant** (`/chat`) answers questions from your organization's documents. Every claim
in an answer is marked with the source it comes from, and the answer only uses sources you are
allowed to read. When the sources don't say, it tells you so instead of guessing.

## Ask a question

Type a question and press Enter. The answer appears as it is written; **Stop** interrupts it.
Follow-up questions can refer to earlier ones ("and for contractors?"): the assistant rewrites
them into a full question before it searches.

Each conversation is private: only you see your conversations, in the list on the left, where
you can rename and delete them. If you leave or reload the page while an answer is being
written, it keeps going on the server and the conversation picks it up when you come back.

## Read the citations

Small numbered markers in the answer point to the passages it is based on. Click one to open
the **Sources** panel at that passage. For each source the panel shows:

- the document's title, its section and page, and the passage itself;
- its **trust level**: *official* (approved, and preferred when sources disagree),
  *reference* (ordinary documentation) or *discussion* (chats, tickets, meeting talk);
- its **sensitivity label**: internal, confidential or restricted;
- a warning when an official document is **past its review date**: its owner has not confirmed
  that it is still correct ([Knowledge governance](../governance.md));
- **Open document**, and for a PDF, **Open PDF at page N**.

Under **Also searched**, the panel lists the passages the search found that the answer did not
cite. If a document changed after the answer was written, the panel says the passage changed;
if you have since lost access to it, it says that too and does not show it.

## What the assistant uses, and what it does not

- **Only what you can read.** The search runs as you: it filters passages by their access list,
  then checks every source again with the authorization service before anything reaches the
  model ([ADR 0006](../adr/0006-two-lock-permission-aware-retrieval.md)). An agent or a tool
  never has more access than the person asking.
- **No hints about what you can't read.** If the answer is only in documents you may not open,
  you get *"I couldn't find this in the sources available to you"*, never "there is a document
  you don't have access to".
- **Official first.** When sources disagree, the answer gives the official one and mentions the
  disagreement, citing both. An answer that relies on a chat or ticket says so.
- **Sources are data.** Instructions written inside a document ("ignore your rules…") are
  ignored.
- **Some sources may be held back.** Your organization decides which sensitivity labels may be
  sent to an external AI model. If a source above that line matched your question and no local
  model is available, it is left out, and a notice above the answer says how many were held
  back and why ([Models and keys](../models.md#the-data-policy-what-may-leave)).

**Checked, and not checked.** Every marker in an answer must point to a source the model was
given; one that doesn't is shown greyed out, as "not among the retrieved sources". WorkMind
does not yet check,
sentence by sentence, that the cited passage supports the claim. How often answers stay faithful
to their sources is measured in the evaluation instead: 99.4% with Claude Sonnet 5.5 on the demo
company ([Evaluation](../evals/README.md)).

## Research & act

The mode switch under the message box has two settings:

- **Answer** (the default): search and answer.
- **Research & act**: the research agent may run several searches and use tools, and can
  prepare an action such as an email or a task. It shows what it did step by step, and each
  proposed action appears as a card in the conversation. **Nothing runs until a person
  approves it**: see [Approvals and tasks](approvals-and-tasks.md).

## Department agents

If your deployment routes questions to department agents (`ASSISTANT_MODE=auto` or `agents`),
the answer shows which agents worked on it, and **How this answer was made** lists what each one
searched, with its findings and timing. Agents only narrow what they search; they never widen
your access ([Agents](../agents.md)).

## Feedback and limits

- **Good answer / Bad answer**, with an optional comment, records feedback on an answer.
  **Copy answer** copies its text.
- If your organization's model budget, or your own cap, is used up, a question is refused with a
  message saying so ([Usage and budgets](../usage.md)). Your usage this month is on the
  Overview page.

## From code

The same answers are available outside the browser:

- **MCP**: the `ask` and `search_knowledge` tools in Claude Code, Cursor or Claude Desktop, with a
  personal access token ([MCP](../mcp.md)).
- **HTTP API**: `POST /api/v1/conversations/{id}/messages` starts an answer and returns a
  `run_id`; `GET /api/v1/runs/{run_id}/events` streams it as server-sent events (reconnect with
  `Last-Event-ID`). `POST /api/v1/ask` returns a whole answer in one response, and
  `POST /api/v1/search` returns the passages without an answer. See the
  [HTTP API reference](../reference/api.md).
