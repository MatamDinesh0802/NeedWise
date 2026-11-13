"""Slack: channel history through the Web API with a bot token (scopes channels:history,
channels:read; add groups:* for private channels the bot was invited to).

Each channel becomes one document per month, in the same format as a Slack export, so threads
stay together and an edited or deleted message changes that month's version."""

import hashlib
import json
import time
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from needwise.modules.connectors.base import (
    ConnectorError,
    ConnectorRefusedError,
    RemoteItem,
    ScanResult,
)

API = "https://slack.com/api"


def _edited(message: dict[str, Any]) -> str | None:
    edited: dict[str, Any] = message.get("edited") or {}
    return edited.get("ts")


class SlackConnector:
    kind = "slack"

    def __init__(
        self, http: httpx.AsyncClient, *, token: str, channels: list[str], days: int = 90
    ) -> None:
        if not token:
            raise ConnectorError("a Slack source needs a bot token (xoxb-...)")
        self._http = http
        self._headers = {"Authorization": f"Bearer {token}"}
        self._channels = channels
        self._days = days
        self._cache: dict[str, bytes] = {}

    async def _call(self, method: str, **params: str) -> dict[str, Any]:
        try:
            response = await self._http.get(
                f"{API}/{method}", params=params, headers=self._headers, timeout=30
            )
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Slack is unreachable: {exc}") from exc
        if response.status_code == 429:
            raise ConnectorError("Slack rate limit reached; the next sync will continue")
        if response.is_error:
            raise ConnectorError(f"Slack answered HTTP {response.status_code}")
        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise ConnectorError(f"Slack {method}: the response was not JSON") from exc
        if not body.get("ok"):
            raise ConnectorRefusedError(f"Slack {method}: {body.get('error', 'failed')}")
        return body

    async def api(self, method: str, **params: str) -> dict[str, Any]:
        """A read-only Web API call with this source's token (permission sync uses it)."""
        return await self._call(method, **params)

    async def _history(self, channel: str) -> list[dict[str, Any]]:
        oldest = str(time.time() - self._days * 86_400)
        messages: list[dict[str, Any]] = []
        cursor = ""
        while True:
            params = {"channel": channel, "oldest": oldest, "limit": "200"}
            if cursor:
                params["cursor"] = cursor
            page = await self._call("conversations.history", **params)
            messages += page.get("messages", [])
            metadata: dict[str, Any] = page.get("response_metadata") or {}
            cursor = str(metadata.get("next_cursor", ""))
            if not cursor:
                return messages

    async def scan(self, cursor: Mapping[str, Any]) -> ScanResult:
        items: list[RemoteItem] = []
        for channel in self._channels:
            info = (await self._call("conversations.info", channel=channel))["channel"]
            name = info.get("name", channel)
            months: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for m in await self._history(channel):
                if m.get("subtype") in ("channel_join", "channel_leave", "bot_add"):
                    continue
                when = datetime.fromtimestamp(float(m["ts"]), UTC)
                months[when.strftime("%Y-%m")].append(
                    {
                        "user": m.get("user", "someone"),
                        "text": m.get("text", ""),
                        "ts": m["ts"],
                        "thread_ts": m.get("thread_ts"),
                        "date": when.strftime("%Y-%m-%d %H:%M"),
                        "edited": _edited(m),
                    }
                )
            for month, entries in sorted(months.items()):
                body = json.dumps({"channel": name, "messages": entries}, sort_keys=True).encode()
                latest = max(float(e["edited"] or e["ts"]) for e in entries)
                external_id = f"{channel}:{month}"
                self._cache[external_id] = body
                items.append(
                    RemoteItem(
                        external_id=external_id,
                        title=f"Slack #{name}, {month}",
                        filename=f"slack-{name}-{month}.json",
                        version=hashlib.sha256(body).hexdigest()[:32],
                        url=f"https://slack.com/app_redirect?channel={channel}",
                        modified_at=datetime.fromtimestamp(latest, UTC),
                    )
                )
        # Only a window of history is read: older months are kept, not treated as deleted.
        return ScanResult(items=items, complete=False)

    async def fetch(self, item: RemoteItem) -> bytes:
        if item.external_id not in self._cache:
            raise ConnectorError(f"{item.external_id} was not part of this scan")
        return self._cache[item.external_id]
