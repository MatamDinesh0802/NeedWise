"""Source health without a database: the status people read, the next scheduled sync, sync
errors in plain words, and the connectors' "cut short" and modification-time signals."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from workmind.modules.connectors.github import GitHubConnector
from workmind.modules.connectors.health import explain, health, next_sync_at
from workmind.modules.connectors.models import Source
from workmind.modules.connectors.slack import SlackConnector
from workmind.modules.connectors.web import WebConnector

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def source(**changes: object) -> Source:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "key": "handbook-site",
        "name": "Handbook site",
        "kind": "web",
        "enabled": True,
        "status": "ok",
        "interval_minutes": 60,
        "consecutive_failures": 0,
        "last_sync_at": NOW - timedelta(minutes=20),
        "last_success_at": NOW - timedelta(minutes=19),
    }
    values.update(changes)
    return Source(**values)


def test_health_is_paused_failing_never_synced_or_healthy() -> None:
    assert health(source()) == "healthy"
    assert health(source(enabled=False, consecutive_failures=4)) == "paused"
    assert health(source(status="error", consecutive_failures=1)) == "failing"
    assert health(source(status="idle", last_sync_at=None, last_success_at=None)) == "never_synced"
    # A first sync that failed is failing, not "never synced": someone has to act.
    assert health(source(status="error", consecutive_failures=1, last_success_at=None)) == (
        "failing"
    )
    # While a sync runs, the health of the previous one stands.
    assert health(source(status="syncing")) == "healthy"


def test_the_next_sync_is_one_interval_after_the_last_one_started() -> None:
    assert next_sync_at(source(), NOW) == NOW + timedelta(minutes=40)
    assert next_sync_at(source(last_sync_at=None, last_success_at=None), NOW) == NOW  # due now
    assert next_sync_at(source(enabled=False), NOW) is None
    assert next_sync_at(source(status="syncing"), NOW) is None
    overdue = next_sync_at(source(last_sync_at=NOW - timedelta(hours=3)), NOW)
    assert overdue is not None
    assert overdue < NOW


@pytest.mark.parametrize(
    ("raw", "words"),
    [
        ("Slack conversations.history: not_in_channel", "/invite"),
        ("Slack conversations.info: invalid_auth", "Reconnect Slack"),
        ("Slack conversations.history: missing_scope", "groups:history"),
        ("Slack conversations.info: channel_not_found", "channel ids"),
        ("a Slack source needs a bot token (xoxb-...)", "Connect Slack"),
        ("connect Google Drive first (OAuth)", "Connect Google Drive"),
        ("Google refused the refresh token; reconnect Google Drive", "Connect Google Drive"),
        ("Google Drive refused access; check the folder is shared", "refused access"),
        ("GitHub refused the credentials", "Check the credentials"),
        ("acme/handbook not found (or the token cannot see it)", "wasn't found"),
        ("GitHub rate limit reached; add a token or wait", "rate limit"),
        ("https://intranet/ is unreachable: ConnectError", "couldn't reach"),
        ("https://intranet/ redirects to another site (evil.example); not followed", "redirects"),
        ("https://169.254.169.254/ is not allowed: link-local", "outbound URL policy"),
        ("Handbook.pdf: larger than 50 MB", "MAX_UPLOAD_BYTES"),
        ("SecretsError: credentials cannot be decrypted", "SECRETS_KEY"),
        ("the source's collection no longer exists", "Disconnect"),
        ("intranet answered HTTP 503", "internal error"),
        ("the worker stopped before the sync finished", "interrupted"),
    ],
)
def test_sync_errors_are_explained_in_plain_words(raw: str, words: str) -> None:
    plain = explain(raw)
    assert plain is not None
    assert words.lower() in plain.lower(), plain


def test_an_unknown_error_still_gets_a_sentence_and_no_error_gets_none() -> None:
    assert "doesn't recognise" in (explain("KeyError: 'files'") or "")
    assert explain(None) is None
    assert explain("") is None


async def test_a_crawl_cut_short_by_max_pages_is_truncated_and_carries_last_modified() -> None:
    def site(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        links = "".join(f"<a href='/docs/p{i}.html'>p{i}</a>" for i in range(5))
        return httpx.Response(
            200,
            headers={
                "content-type": "text/html",
                "last-modified": "Tue, 03 Nov 2026 10:00:00 GMT",
            },
            text=f"<html><title>{request.url.path}</title>{links}</html>",
        )

    async def allowed(url: str) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
        cut = await WebConnector(
            http, start_urls=["https://intranet.example/docs/"], max_pages=2, check=allowed
        ).scan({})
        whole = await WebConnector(
            http, start_urls=["https://intranet.example/docs/"], max_pages=50, check=allowed
        ).scan({})

    assert (cut.complete, cut.truncated, len(cut.items)) == (False, True, 2)
    assert (whole.complete, whole.truncated) == (True, False)
    assert cut.items[0].modified_at == datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


async def test_a_truncated_github_tree_is_truncated() -> None:
    def github(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/handbook":
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(
            200,
            json={
                "truncated": True,
                "tree": [{"path": "docs/leave.md", "type": "blob", "sha": "abc"}],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(github)) as http:
        scan = await GitHubConnector(http, repo="acme/handbook").scan({})

    assert (scan.complete, scan.truncated) == (False, True)


async def test_slack_history_is_a_window_not_a_truncation_and_months_carry_their_last_edit() -> (
    None
):
    now = datetime.now(UTC).timestamp()

    def slack(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("conversations.info"):
            return httpx.Response(200, json={"ok": True, "channel": {"name": "general"}})
        return httpx.Response(
            200,
            content=json.dumps(
                {
                    "ok": True,
                    "messages": [
                        {
                            "ts": f"{now - 120:.6f}",
                            "user": "U2",
                            "text": "edited",
                            "edited": {"ts": f"{now - 30:.6f}"},
                        },
                    ],
                }
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(slack)) as http:
        scan = await SlackConnector(http, token="xoxb-test", channels=["C0123ABCD"]).scan({})

    assert (scan.complete, scan.truncated) == (False, False)  # older months are not suspects
    modified = scan.items[-1].modified_at
    assert modified is not None
    assert abs(modified.timestamp() - (now - 30)) < 1
