# NeedWise over MCP

NeedWise is an MCP server at **`/mcp`** (Streamable HTTP). Any MCP client — Claude Code,
Cursor, Claude Desktop, your own scripts — can ask questions of your company's documents with
**the same permissions as the web app**: both permission locks, the tenant's model policy and the
token's scopes apply, and every call is audited: `ask` as `assistant.answered` with `via: mcp`
(the documents the answer drew on, never its text, as for the web app), the other tools as
`tool.called` with `caller: mcp`.

## 1. Create a personal access token

In NeedWise: **Settings → Access tokens → New token**. Pick the scopes:

| Scope | Allows |
|---|---|
| `knowledge:read` | `search_knowledge`, `list_collections` |
| `assistant:ask` | `ask` (cited answers; uses the configured model); `describe_data`, `query_data` if you have access to the analytics data |

The token (`nw_pat_...`) is shown once. It acts as you, never with more access, and can be
revoked at any time.

## 2. Connect a client

**Claude Code**

```bash
claude mcp add --transport http needwise http://localhost:8480/mcp \
  --header "Authorization: Bearer nw_pat_..."
```

**Cursor** (`.cursor/mcp.json` or the global `~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "needwise": {
      "url": "http://localhost:8480/mcp",
      "headers": { "Authorization": "Bearer nw_pat_..." }
    }
  }
}
```

**Claude Desktop.** Use a local bridge for token-authenticated servers
(`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "needwise": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:8480/mcp", "--header", "Authorization:${NEEDWISE_AUTH}"],
      "env": { "NEEDWISE_AUTH": "Bearer nw_pat_..." }
    }
  }
}
```

**Python** (the official SDK)

```python
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

http = httpx2.AsyncClient(headers={"Authorization": "Bearer nw_pat_..."})
async with Client(streamable_http_client("http://localhost:8480/mcp", http_client=http)) as c:
    print((await c.call_tool("ask", {"question": "How many weeks of parental leave?"})).content)
```

## Tools

| Tool | What it does |
|---|---|
| `ask(question)` | A cited answer from the documents you may read, or an honest "not found" |
| `search_knowledge(query, limit)` | Raw passages with their source titles |
| `list_collections()` | The collections you can read |
| `describe_data()` | The analytics data you may query: metrics, dimensions, tables, your row limits |
| `query_data(metrics, dimensions, filters, date_from, date_to, order_by, limit)` | A semantic query (metrics by dimensions) run as you: only the rows your row policies allow |

`describe_data` and `query_data` appear in `tools/list` only for token owners whom the
organization's semantic model gives access to the analytics data ([analytics.md](analytics.md)),
and only with the `assistant:ask` scope. A client that calls them anyway is refused by the
analytics service with the reason.

## Security notes

- No token, an invalid one or a revoked one gets HTTP 401 before any MCP processing.
- The server accepts only known hosts (`PUBLIC_URL` and localhost) against DNS rebinding.
- Results are company data. The server's instructions tell clients to treat any instructions
  inside them as text.

## The other direction: NeedWise using MCP tools

Operators can connect remote MCP servers for NeedWise's research agent (`POST /api/v1/research`):

```bash
MCP_SERVERS='[{"key": "erp", "url": "https://erp.internal/mcp", "token": "...", "read_only": ["robot_stock"]}]'
```

- Each server's tools are imported as `<key>__<tool>`.
- Every remote tool is **external**, and stops at the approval gate, unless you list it in
  `read_only`. A server's own read-only hint is not trusted.
- `GET /api/v1/admin/tools` lists every tool with its side-effect level.
