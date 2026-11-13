"""A small Slack client for notifications (docs/automation.md): a direct message through a bot
token, or a channel post through an incoming webhook. Nothing else of Slack's API is used.

    direct message: users.lookupByEmail (the person's WorkMind address) -> chat.postMessage
    channel post:   POST <incoming webhook URL> {"text": ...}

Slack answers most API errors with HTTP 200 and {"ok": false, "error": "..."}. Rate limits (429)
and server errors are worth retrying; a wrong token or an unknown address is not.

Errors never contain the token or the webhook URL (both are secrets).
"""

from dataclasses import dataclass
from typing import Any

import httpx

TIMEOUT = 10.0
# Errors that retrying cannot fix: the token or webhook is wrong, the person is not in Slack.
PERMANENT = frozenset(
    {
        "invalid_auth",
        "not_authed",
        "account_inactive",
        "token_revoked",
        "missing_scope",
        "users_not_found",
        "user_not_found",
        "channel_not_found",
        "no_service",
        "invalid_token",
        "action_prohibited",
        "posting_to_general_channel_denied",
    }
)


class SlackError(Exception):
    def __init__(self, message: str, *, retry: bool) -> None:
        super().__init__(message)
        self.retry = retry


@dataclass(frozen=True)
class SlackClient:
    http: httpx.AsyncClient
    api_url: str = "https://slack.com/api"

    async def _call(self, method: str, token: str, **payload: Any) -> dict[str, Any]:
        try:
            response = await self.http.post(
                f"{self.api_url.rstrip('/')}/{method}",
                headers={"Authorization": f"Bearer {token}"},
                data=payload if method == "users.lookupByEmail" else None,
                json=None if method == "users.lookupByEmail" else payload,
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise SlackError(f"Slack is unreachable ({type(exc).__name__})", retry=True) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise SlackError(f"Slack answered {response.status_code}", retry=True)
        try:
            body: dict[str, Any] = response.json()
        except ValueError as exc:
            raise SlackError(f"Slack answered {response.status_code}", retry=True) from exc
        if not body.get("ok"):
            error = str(body.get("error") or f"http_{response.status_code}")
            raise SlackError(f"Slack refused {method}: {error}", retry=error not in PERMANENT)
        return body

    async def check_token(self, token: str) -> str:
        """The workspace's name, if the bot token works (auth.test)."""
        body = await self._call("auth.test", token)
        return str(body.get("team") or "Slack")

    async def direct_message(self, token: str, email: str, text: str) -> None:
        found = await self._call("users.lookupByEmail", token, email=email)
        user = found.get("user")
        user_id = user.get("id") if isinstance(user, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if not isinstance(user_id, str):
            raise SlackError("Slack refused users.lookupByEmail: users_not_found", retry=False)
        await self._call("chat.postMessage", token, channel=user_id, text=text)

    async def post_webhook(self, url: str, text: str) -> None:
        try:
            response = await self.http.post(url, json={"text": text}, timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise SlackError(
                f"The Slack webhook is unreachable ({type(exc).__name__})", retry=True
            ) from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise SlackError(f"The Slack webhook answered {response.status_code}", retry=True)
        if response.status_code != 200:
            # 400 invalid_payload, 403 action_prohibited, 404 no_service, 410 channel_is_archived
            reason = response.text.strip()[:60] or str(response.status_code)
            raise SlackError(f"The Slack webhook refused the message: {reason}", retry=False)


def webhook_allowed(url: str, prefix: str) -> bool:
    """Incoming webhooks only at the configured prefix (SLACK_WEBHOOK_PREFIX): a tenant admin
    cannot make the server post to any address."""
    return url.startswith(prefix) and len(url) <= 500 and not any(c.isspace() for c in url)
