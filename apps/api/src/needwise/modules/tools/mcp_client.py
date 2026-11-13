"""Tools from remote MCP servers (ARCHITECTURE 13.4), configured by the operator.

Each server's tools are imported as `<server key>__<tool name>`, so a remote server can never
shadow a built-in or another server's tool. A remote server is a third party: its descriptions
and outputs are untrusted, and its own `readOnlyHint` is not believed. Every remote tool is
"external" (approval-gated) unless the operator lists it as read-only in the configuration.
"""

import html
import json
from dataclasses import dataclass, field
from typing import Any

import httpx2
import structlog
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from pydantic import BaseModel, Field

from needwise.core.context import RequestContext
from needwise.modules.assistant.safety import quote_markers
from needwise.modules.tools.registry import Tool, ToolError, ToolRegistry, ToolResult

log = structlog.get_logger("needwise.mcp_client")


class RemoteServerConfig(BaseModel):
    """One entry of MCP_SERVERS (a JSON list in the environment)."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,30}$")
    url: str
    token: str | None = None  # sent as a bearer token
    read_only: list[str] = []  # tool names (without the prefix) trusted to change nothing
    scope: str = "assistant:ask"  # the NeedWise scope a caller needs to use these tools


@dataclass
class RemoteServer:
    config: RemoteServerConfig
    in_process: MCPServer | None = None  # tests and demos: no network
    tool_names: list[str] = field(default_factory=list[str])

    def client(self) -> Client:
        if self.in_process is not None:
            return Client(self.in_process)
        headers = {"Authorization": f"Bearer {self.config.token}"} if self.config.token else {}
        http = httpx2.AsyncClient(headers=headers, timeout=30)
        return Client(streamable_http_client(self.config.url, http_client=http))


def _text(content: list[Any]) -> str:
    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        parts.append(
            text if isinstance(text, str) else json.dumps(getattr(item, "model_dump", dict)())
        )
    return "\n".join(parts)


def _remote_tool(server: RemoteServer, name: str, description: str, schema: dict[str, Any]) -> Tool:
    config = server.config

    async def run(ctx: RequestContext, args: Any) -> ToolResult:
        try:
            async with server.client() as client:
                result = await client.call_tool(name, args)
        except Exception as exc:  # network, protocol: the caller sees a clean error
            raise ToolError("upstream_unavailable", f"{config.key} is unavailable") from exc
        text = _text(list(result.content))
        if result.is_error:
            raise ToolError("tool_failed", f"{config.key}.{name}: {text[:300]}")
        # A remote server is a third party: its text cannot close the element (or pre-write
        # citation markers) and pose as instructions.
        quoted = html.escape(quote_markers(text), quote=False)
        return ToolResult(f'<external source="{config.key}">\n{quoted}\n</external>')

    return Tool(
        name=f"{config.key}__{name}",
        description=f"[{config.key}, external] {description}"[:1000],
        input_model=None,
        json_schema=schema or {"type": "object", "properties": {}},
        side_effect="read" if name in config.read_only else "external",
        scope=config.scope,
        handler=run,
        source=f"mcp:{config.key}",
    )


async def import_tools(registry: ToolRegistry, server: RemoteServer) -> list[str]:
    """(Re)loads one server's tools into the registry. Returns the imported names."""
    async with server.client() as client:
        listed = await client.list_tools()
    tools = [
        _remote_tool(server, t.name, t.description or "", dict(t.input_schema or {}))
        for t in listed.tools
    ]
    registry.replace_source(f"mcp:{server.config.key}", tools)
    server.tool_names = [t.name for t in tools]
    log.info("mcp_tools_imported", server=server.config.key, tools=server.tool_names)
    return server.tool_names
