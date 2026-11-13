"""Source health as people read it (docs/connectors.md, Admin > Sources).

- `health()`: healthy, failing, paused or never synced, from what the sync engine recorded.
- `next_sync_at()`: when the scheduler (Celery beat, every minute) will pick the source up.
- `explain()`: a sync error in plain words, with what to do about it. The raw message is kept
  (run history, audit log); this is what a person reads first.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from needwise.modules.connectors.models import Source

Health = Literal["healthy", "failing", "paused", "never_synced"]
StaleReason = Literal["not_updated", "missing_upstream"]

# Without a threshold on the source, the stale-documents list still needs a window.
DEFAULT_STALE_DAYS = 180


def health(source: Source) -> Health:
    """Paused wins (nothing will run); then failing (the last finished sync failed); then
    never synced (no sync has succeeded yet); else healthy. A sync in progress keeps the health
    of the previous one."""
    if not source.enabled:
        return "paused"
    if source.consecutive_failures > 0:
        return "failing"
    if source.last_success_at is None:
        return "never_synced"
    return "healthy"


def next_sync_at(source: Source, now: datetime | None = None) -> datetime | None:
    """When the next scheduled sync is due: one interval after the last one started (the
    scheduler's rule in schedule.py). None while paused or while a sync is running. A time in
    the past means "due": the scheduler picks it up within a minute."""
    if not source.enabled or source.status == "syncing":
        return None
    if source.last_sync_at is None:
        return now or datetime.now(UTC)
    return source.last_sync_at + timedelta(minutes=source.interval_minutes)


# (pattern, explanation): the first match wins. Patterns are matched case-insensitively against
# the raw message the connector or the engine raised (modules/connectors/*.py, sync.py).
_EXPLANATIONS: tuple[tuple[str, str], ...] = (
    (
        r"permissions not read",
        "The source didn't show who may see an item (permission sync), so nobody in NeedWise "
        "sees it until a sync can read them. Check that the account may read sharing settings.",
    ),
    (
        r"cannot be decrypted|credentials are malformed",
        "The stored credentials can't be read any more (the server's SECRETS_KEY changed). "
        "Enter them again, or reconnect.",
    ),
    (
        r"connect google drive first|reconnect google drive|refused the refresh token",
        "Google Drive isn't connected, or the connection expired. Choose Connect Google Drive.",
    ),
    (
        r"needs a bot token",
        "Slack isn't connected. Connect Slack, or enter a bot token under Credentials.",
    ),
    (
        r"not_in_channel",
        "The Slack app isn't a member of one of the channels. Invite it to the channel "
        "(/invite @your-app) and sync again.",
    ),
    (
        r"invalid_auth|not_authed|token_revoked|account_inactive|token_expired",
        "Slack refused the token: it was revoked, or the app was removed from the workspace. "
        "Reconnect Slack or enter a new bot token.",
    ),
    (
        r"missing_scope",
        "The Slack token lacks a permission it needs (channels:history and channels:read; "
        "groups:history and groups:read for private channels). Reconnect Slack.",
    ),
    (
        r"channel_not_found",
        "A Slack channel wasn't found: check the channel ids, and that the app can see the "
        "channel (private channels need an invitation).",
    ),
    (
        r"rate limit",
        "The source asked NeedWise to slow down (rate limit). Nothing is lost: the next sync "
        "continues.",
    ),
    (
        r"refused the credentials|refused access|http 401|http 403",
        "The source refused access. Check the credentials (or reconnect), and that the "
        "account can see what this source syncs.",
    ),
    (
        r"not found|http 404",
        "What this source points at wasn't found: check the repository, folder, channel or "
        "start URL, and that the account can see it.",
    ),
    (
        r"redirects to another site",
        "A start URL redirects to another site, which the crawler doesn't follow. Use the "
        "address it redirects to as the start URL.",
    ),
    (
        r"is not allowed",
        "A start URL points at an address NeedWise may not fetch (the outbound URL policy). "
        "Use the site's public or intranet address.",
    ),
    (
        r"unreachable|timed out|timeout|connecterror|connection",
        "NeedWise couldn't reach the source: it may be down, or blocked from this server. "
        "The next sync tries again.",
    ),
    (
        r"larger than",
        "A file was larger than the upload limit (MAX_UPLOAD_BYTES), so it was skipped.",
    ),
    (
        r"collection no longer exists",
        "The collection this source syncs into was deleted. Disconnect the source.",
    ),
    (
        r"unsupported_type|not supported|empty_file|the file is empty",
        "A file's type isn't supported, or it was empty, so it was skipped.",
    ),
    (
        r"http 5\d\d",
        "The source had an internal error. The next sync tries again.",
    ),
    (
        r"worker stopped",
        "The sync was interrupted (the worker stopped before it finished). The next sync "
        "starts again.",
    ),
)


def explain(message: str | None) -> str | None:
    """A sync error in plain words. Unknown errors get a generic sentence; the raw message is
    shown next to it."""
    if not message:
        return None
    for pattern, explanation in _EXPLANATIONS:
        if re.search(pattern, message, re.IGNORECASE):
            return explanation
    return (
        "The sync failed for a reason NeedWise doesn't recognise. The details say what the "
        "source answered."
    )
