"""Built-in tools (ARCHITECTURE 13.3). Each one acts as the requesting user."""

import html
from typing import Any

import httpx
from pydantic import BaseModel, Field

from workmind.core.context import RequestContext
from workmind.modules.assistant.safety import quote_markers
from workmind.modules.retrieval.service import Retriever
from workmind.modules.tools.registry import Tool, ToolError, ToolResult


class SearchKnowledge(BaseModel):
    query: str = Field(min_length=1, max_length=1000, description="what to look for")
    limit: int = Field(default=6, ge=1, le=12)


class DraftEmail(BaseModel):
    to: list[str] = Field(min_length=1, max_length=20, description="recipient addresses")
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)


class WebSearch(BaseModel):
    query: str = Field(min_length=1, max_length=400)
    limit: int = Field(default=5, ge=1, le=10)


def search_knowledge_tool(retriever: Retriever) -> Tool:
    async def run(ctx: RequestContext, args: Any) -> ToolResult:
        assert isinstance(args, SearchKnowledge)  # noqa: S101  (validated by the wrapper)
        found = await retriever.search(ctx, args.query, limit=args.limit)
        if not found.passages:
            return ToolResult("No documents you can access match this search.", {"results": []})
        blocks = [
            f'<result source="{html.escape(p.title)}" section="{html.escape(p.section)}" '
            f'document_id="{p.document_id}" trust="{p.trust}">\n'
            f"{html.escape(quote_markers(p.text))}\n</result>"
            for p in found.passages
        ]
        data = {
            "results": [
                {
                    "document_id": p.document_id,
                    "title": p.title,
                    "section": p.section,
                    "page": p.page,
                    "trust": p.trust,
                    "text": p.text,
                }
                for p in found.passages
            ]
        }
        return ToolResult("\n\n".join(blocks), data)

    return Tool(
        name="search_knowledge",
        description=(
            "Search the company's documents that the current user may read. Returns passages "
            "with their source titles. Use it for any question about company policy, products, "
            "customers or internal facts."
        ),
        input_model=SearchKnowledge,
        side_effect="read",
        scope="knowledge:read",
        handler=run,
    )


def draft_email_tool() -> Tool:
    """Drafting changes nothing: the draft is returned for a person to review and send."""

    async def run(ctx: RequestContext, args: Any) -> ToolResult:
        assert isinstance(args, DraftEmail)  # noqa: S101
        text = f"To: {', '.join(args.to)}\nSubject: {args.subject}\n\n{args.body}"
        return ToolResult(f"Draft ready for review (not sent):\n\n{text}", args.model_dump())

    return Tool(
        name="draft_email",
        description=(
            "Prepare an email draft for the user to review. It is never sent automatically."
        ),
        input_model=DraftEmail,
        side_effect="read",
        scope="assistant:ask",
        handler=run,
    )


def web_search_tool(http: httpx.AsyncClient, searxng_url: str) -> Tool:
    """Public web search through a self-hosted SearXNG. External: the query leaves WorkMind."""

    async def run(ctx: RequestContext, args: Any) -> ToolResult:
        assert isinstance(args, WebSearch)  # noqa: S101
        try:
            response = await http.get(
                searxng_url.rstrip("/") + "/search",
                params={"q": args.query, "format": "json"},
                timeout=20,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolError("upstream_unavailable", "Web search is unavailable") from exc
        results = response.json().get("results", [])[: args.limit]
        lines = [
            f"- {r.get('title', '')} <{r.get('url', '')}>: {r.get('content', '')}" for r in results
        ]
        return ToolResult(
            "Web results (untrusted, not company knowledge):\n" + "\n".join(lines or ["- none"]),
            {"results": results},
        )

    return Tool(
        name="web_search",
        description="Search the public web. Results are not company knowledge.",
        input_model=WebSearch,
        side_effect="external",
        scope="assistant:ask",
        handler=run,
    )
