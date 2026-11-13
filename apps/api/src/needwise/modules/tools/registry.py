"""Tools that agents and MCP clients can call (ARCHITECTURE section 13).

Every tool declares its side-effect level, and the execution wrapper enforces what that means:

    read      looks things up, never changes anything          runs directly
    write     changes something inside NeedWise (a task)        needs approval, unless policy allows
    external  leaves NeedWise (an email, a web request)         needs approval, unless policy allows

Tools act as the requesting user (RequestContext): a tool can never see or do more than the
person asking. Tool output is untrusted data: it is size-limited, stripped of control characters,
and handed to models inside a delimited element.
"""

import re
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel

from needwise.core.context import RequestContext

SideEffect = Literal["read", "write", "external"]
MAX_OUTPUT_CHARS = 12_000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ToolError(Exception):
    """A failure the caller may show: bad input, not allowed, upstream down."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolResult:
    content: str
    data: dict[str, Any] | None = None  # structured result for programmatic callers


Handler = Callable[[RequestContext, Any], Awaitable[ToolResult]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str  # written for a model choosing a tool
    input_model: type[BaseModel] | None  # built-in tools; remote tools bring a JSON schema
    side_effect: SideEffect
    scope: str  # access-token scope required, e.g. "knowledge:read"
    handler: Handler
    source: str = "builtin"  # or "mcp:<server key>"
    json_schema: dict[str, Any] | None = None
    # Plain-words description of one call, shown on its approval card.
    summarize: Callable[[dict[str, Any]], str] | None = None
    # Whether this person may use the tool at all (e.g. analytics access), beyond the scope.
    # A tool that is not available is not offered to models or listed to MCP clients. Its
    # handler still enforces permissions itself: this only hides what would be refused.
    available: Callable[[RequestContext], Awaitable[bool]] | None = None

    def describe(self, arguments: dict[str, Any]) -> str:
        if self.summarize is not None:
            return self.summarize(arguments)
        shown = ", ".join(f"{k}={str(v)[:60]}" for k, v in list(arguments.items())[:4])
        return f"Run {self.name} ({shown})"

    def input_schema(self) -> dict[str, Any]:
        if self.input_model is not None:
            return self.input_model.model_json_schema()
        return self.json_schema or {"type": "object", "properties": {}}

    def validate(self, arguments: dict[str, Any]) -> Any:
        """Raises ValueError with a readable reason if the input does not fit the schema."""
        if self.input_model is not None:
            return self.input_model.model_validate(arguments)
        validator = Draft202012Validator(self.input_schema())
        messages = [str(e.message) for e in validator.iter_errors(arguments)]  # pyright: ignore[reportUnknownMemberType]  (jsonschema's overloads)
        if messages:
            raise ValueError("; ".join(messages[:3]))
        return arguments


def sanitize(text: str) -> str:
    """Output is data for a model: no control characters, bounded size."""
    clean = _CONTROL.sub("", text)
    if len(clean) > MAX_OUTPUT_CHARS:
        clean = clean[:MAX_OUTPUT_CHARS] + "\n[output truncated]"
    return clean


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def replace_source(self, source: str, tools: list[Tool]) -> None:
        """Swap every tool of one source (e.g. a remote MCP server) atomically."""
        kept = {name: t for name, t in self._tools.items() if t.source != source}
        for tool in tools:
            if tool.name in kept:
                raise ValueError(
                    f"tool {tool.name!r} from {source} clashes with {kept[tool.name].source}"
                )
            kept[tool.name] = tool
        self._tools = kept

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return sorted(self._tools.values(), key=lambda t: t.name)

    async def usable(self, ctx: RequestContext, names: Collection[str] | None = None) -> list[Tool]:
        """The tools this person may use (scope, then availability), optionally only `names`."""
        usable: list[Tool] = []
        for tool in self.all():
            if names is not None and tool.name not in names:
                continue
            if not ctx.has_scope(tool.scope):
                continue
            if tool.available is not None and not await tool.available(ctx):
                continue
            usable.append(tool)
        return usable
