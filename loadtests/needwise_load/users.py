"""The Locust users: one class per kind of person, each doing what that person does in the web
app, with the same API calls (docs/load-testing.md has the full list).

Most users authenticate with a personal access token (Settings > Access tokens; `tokens.py`
creates them), the way scripts and MCP clients do: the API serves token and session requests
through the same handlers. Endpoints that accept only a browser session (uploads, admin pages,
creating tokens) are reached by signing in through Keycloak first (`browser.py`).

Each request is named "<area>: <action>" in Locust's statistics. Custom metrics (request type
SSE, INGEST or FLOW) record what a person waits for: time to first token, answer complete,
time until an upload is searchable, a whole sign-in.

Tasks that call the language model are tagged "model"; `--nw-no-model` turns them off.
"""

import itertools
import logging
import random
import sys
import time
import uuid
from argparse import Namespace
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import gevent
import requests
from locust import HttpUser, constant_pacing, tag, task
from locust.env import Environment
from locust.exception import StopUser

from needwise_load import sse
from needwise_load.browser import SignInError, browser_like, same_origin, sign_in, sign_out
from needwise_load.questions import Question, QuestionBank, pick
from needwise_load.settings import (
    CONVERSATION_PREFIX,
    DEMO_USERS,
    UPLOAD_PREFIX,
    Settings,
    SettingsError,
    parse_mix,
    parse_range,
)
from needwise_load.tokens import TokenFile

log = logging.getLogger("needwise_load")
POLL_INTERVAL_S = 30  # the web app's notification bell (apps/web/src/lib/work/notifications.ts)
UPLOAD_POLL_S = 1.0


# --- Shared state, set once by the init event (locustfile.py) --------------------------------


@dataclass
class State:
    settings: Settings = field(default_factory=Settings)
    tokens: dict[str, str] = field(default_factory=dict[str, str])  # email -> plain token
    bank: QuestionBank = field(default_factory=lambda: QuestionBank({}))
    _turns: dict[str, Iterator[int]] = field(default_factory=dict[str, Iterator[int]])

    def assign(self, kind: str, candidates: list[str]) -> str:
        """Spreads the users of one class over the logins round-robin."""
        if not candidates:
            raise StopUser
        turn = next(self._turns.setdefault(kind, itertools.count()))
        return candidates[turn % len(candidates)]


STATE = State()


def add_options(parser: Any) -> None:
    """Locust command-line options; each also reads its environment variable."""
    group = parser  # not an argument group: only Locust's own add_argument knows is_secret
    group.add_argument("--nw-tokens", env_var="NW_LOAD_TOKENS", default=str(Settings.tokens),
                       help="token file from `tokens create`")  # fmt: skip
    group.add_argument("--nw-mix", env_var="NW_LOAD_MIX", default="",
                       help="class weights, e.g. reader=10,chat=3")  # fmt: skip
    group.add_argument("--nw-no-model", env_var="NW_LOAD_NO_MODEL", action="store_true",
                       help="skip everything that calls the language model")  # fmt: skip
    group.add_argument("--nw-think", env_var="NW_LOAD_THINK", default="3,10",
                       help="seconds between a person's actions: min,max")  # fmt: skip
    group.add_argument("--nw-datasets", env_var="NW_LOAD_DATASETS", default=str(Settings.datasets))
    group.add_argument("--nw-answer-timeout", env_var="NW_LOAD_ANSWER_TIMEOUT", type=float,
                       default=Settings.answer_timeout_s)  # fmt: skip
    group.add_argument("--nw-follow-up", env_var="NW_LOAD_FOLLOW_UP", type=float,
                       default=Settings.follow_up, help="share of follow-up questions")  # fmt: skip
    group.add_argument("--nw-upload-collection", env_var="NW_LOAD_UPLOAD_COLLECTION", default="",
                       help="collection key to upload to; unset: no uploads")  # fmt: skip
    group.add_argument("--nw-uploader", env_var="NW_LOAD_UPLOADER", default=Settings.uploader)
    group.add_argument("--nw-admin", env_var="NW_LOAD_ADMIN", default=Settings.admin)
    group.add_argument("--nw-password", env_var="DEMO_PASSWORD", default=Settings.password,
                       is_secret=True, help="the demo logins' password")  # fmt: skip


def settings_from(options: Namespace | None) -> Settings:
    if options is None:
        return Settings()
    return Settings(
        tokens=Path(options.nw_tokens),
        mix=parse_mix(options.nw_mix),
        no_model=bool(options.nw_no_model),
        think=parse_range(options.nw_think),
        datasets=Path(options.nw_datasets),
        answer_timeout_s=float(options.nw_answer_timeout),
        follow_up=float(options.nw_follow_up),
        upload_collection=options.nw_upload_collection or None,
        uploader=options.nw_uploader,
        admin=options.nw_admin,
        password=options.nw_password,
    )


def configure(environment: Environment) -> None:
    """Reads the options, the token file and the question sets; sets each class's weight."""
    try:
        settings = settings_from(environment.parsed_options)
    except SettingsError as exc:
        sys.exit(f"needwise_load: {exc}")
    STATE.settings = settings
    STATE.bank = QuestionBank.load(settings.datasets)
    weights = settings.weights
    for user_class in environment.user_classes:
        kind = getattr(user_class, "kind", "")
        if kind in weights:
            user_class.weight = weights[kind]
    needs_tokens = any(
        getattr(c, "uses_token", False) and c.weight > 0 for c in environment.user_classes
    )
    if needs_tokens:
        if not settings.tokens.exists():
            sys.exit(f"needwise_load: no token file at {settings.tokens}: run `tokens create`")
        STATE.tokens = {t.email: t.token for t in TokenFile.load(settings.tokens).tokens}
    environment.user_classes[:] = [c for c in environment.user_classes if c.weight > 0]
    if not environment.user_classes:
        sys.exit("needwise_load: every user class has weight 0")
    if settings.no_model:
        environment.exclude_tags = [*(environment.exclude_tags or []), "model"]
    log.info("NeedWise load test: %s", {c.__name__: c.weight for c in environment.user_classes})


def record(
    environment: Environment, kind: str, name: str, seconds: float | None, error: str | None = None
) -> None:
    """A custom metric in Locust's statistics (and a failure when `error` is set)."""
    environment.events.request.fire(
        request_type=kind,
        name=name,
        response_time=(seconds or 0.0) * 1000,
        response_length=0,
        exception=RuntimeError(error) if error else None,
        context={},
    )


def usage_range(today: date | None = None) -> dict[str, str]:
    """The last 30 days, as the Analytics page shows by default."""
    last = today or datetime.now(UTC).date()
    return {"from": (last - timedelta(days=29)).isoformat(), "to": last.isoformat()}


# --- The users -----------------------------------------------------------------------------


class NeedWiseUser(HttpUser):
    abstract = True
    kind = ""
    uses_token = True

    def __init__(self, environment: Environment) -> None:
        super().__init__(environment)
        self.email = ""
        self.rng = random.Random()  # noqa: S311  (load-test choices, not security)

    def wait_time(self) -> float:
        low, high = STATE.settings.think
        return self.rng.uniform(low, high)

    def candidates(self) -> list[str]:
        return sorted(STATE.tokens)

    def on_start(self) -> None:
        self.email = STATE.assign(self.kind, self.candidates())
        if self.uses_token:
            self.client.headers["Authorization"] = f"Bearer {STATE.tokens[self.email]}"

    def questions(self, kind: str) -> list[Question]:
        return STATE.bank.for_login(kind, self.email)

    def get_json(self, path: str, name: str, **kwargs: Any) -> Any:
        with self.client.get(path, name=name, catch_response=True, **kwargs) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")
                return None
            return response.json()


class Reader(NeedWiseUser):
    """Browses: the chat list, collections and documents, search, the Analytics page."""

    kind = "reader"

    def __init__(self, environment: Environment) -> None:
        super().__init__(environment)
        self.conversations: list[str] = []
        self.collections: list[str] = []
        self.documents: list[str] = []

    @task(4)
    def list_conversations(self) -> None:
        page = self.get_json("/api/v1/conversations", "conversations: list", params={"limit": 30})
        if page:
            self.conversations = [c["id"] for c in page["items"]]

    @task(1)
    def open_conversation(self) -> None:
        if self.conversations:
            conversation = self.rng.choice(self.conversations)
            self.get_json(f"/api/v1/conversations/{conversation}", "conversations: open")

    @task(3)
    def list_collections(self) -> None:
        page = self.get_json("/api/v1/collections", "collections: list")
        if page:
            # Not the upload scenario's collection: its documents are deleted mid-run, so
            # opening one later is a correct 404, not a failure of the stack.
            skip = STATE.settings.upload_collection
            self.collections = [c["id"] for c in page["items"] if c.get("key") != skip]

    @task(3)
    def list_documents(self) -> None:
        if not self.collections:
            return self.list_collections()
        collection = self.rng.choice(self.collections)
        page = self.get_json(f"/api/v1/collections/{collection}/documents", "documents: list")
        if page:
            self.documents = [d["id"] for d in page["items"]][:50]

    @task(2)
    def open_document(self) -> None:
        if self.documents:
            self.get_json(f"/api/v1/documents/{self.rng.choice(self.documents)}", "documents: open")

    @task(4)
    def search(self) -> None:
        question = pick(self.questions("chat"), self.rng)
        with self.client.post(
            "/api/v1/search",
            json={"query": question.text, "limit": 8},
            name="search",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")

    @task(1)
    def me(self) -> None:
        self.get_json("/api/v1/me", "me")

    @task(1)
    def agents(self) -> None:
        self.get_json("/api/v1/agents", "agents: list")

    @task(1)
    def usage(self) -> None:
        params = {**usage_range(), "group_by": "feature", "interval": "day"}
        self.get_json("/api/v1/usage/summary", "usage: summary", params=params)


class Poller(NeedWiseUser):
    """An open browser tab: the notification bell and pending approvals every 30 seconds."""

    kind = "poller"
    wait_time = constant_pacing(POLL_INTERVAL_S)  # type: ignore[assignment]

    def on_start(self) -> None:
        super().on_start()
        gevent.sleep(self.rng.uniform(0, POLL_INTERVAL_S))  # tabs were not all opened at once

    @task
    def poll(self) -> None:
        self.get_json("/api/v1/notifications", "notifications: poll")
        self.get_json("/api/v1/approvals", "approvals: pending", params={"status": "pending"})


class AskingUser(NeedWiseUser):
    """Asks in the chat: a conversation, the question, then the answer's event stream."""

    abstract = True

    def __init__(self, environment: Environment) -> None:
        super().__init__(environment)
        self.conversation: str | None = None
        self.created: list[str] = []

    def ask(self, question: str, label: str, *, mode: str = "answer", follow_up: bool) -> None:
        if self.conversation is None or not follow_up:
            with self.client.post(
                "/api/v1/conversations",
                json={"title": (CONVERSATION_PREFIX + question)[:200]},
                name="conversations: create",
                catch_response=True,
            ) as response:
                if response.status_code != 201:
                    response.failure(f"HTTP {response.status_code}: {response.text[:200]}")
                    return
                self.conversation = str(response.json()["id"])
                self.created.append(self.conversation)
        started = time.perf_counter()
        with self.client.post(
            f"/api/v1/conversations/{self.conversation}/messages",
            json={"content": question, "mode": mode},
            name=f"{label}: send question",
            catch_response=True,
        ) as response:
            if response.status_code != 202:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")
                self.conversation = None
                return
            run_id = response.json()["run_id"]
        timing = self.follow(run_id, label, started)
        waited = time.perf_counter() - started  # a failure is timed until it was noticed
        failed = None if timing.ok else (timing.error or timing.status)
        if timing.sources_s is not None:  # research mode searches inside its tool loop instead
            record(self.environment, "SSE", f"{label}: time to sources", timing.sources_s)
        if timing.first_token_s is not None:
            record(self.environment, "SSE", f"{label}: time to first token", timing.first_token_s)
        else:
            record(self.environment, "SSE", f"{label}: time to first token", waited,
                   failed or "no token")  # fmt: skip
        record(self.environment, "SSE", f"{label}: answer complete",
               timing.total_s if timing.total_s is not None else waited, failed)  # fmt: skip
        if timing.ok:
            self.get_json(f"/api/v1/conversations/{self.conversation}", "conversations: open")
        else:
            self.conversation = None

    def follow(self, run_id: str, label: str, started: float) -> sse.RunTiming:
        try:
            with self.client.get(
                f"/api/v1/runs/{run_id}/events",
                name=f"{label}: open stream",
                stream=True,
                headers={"Accept": "text/event-stream"},
                timeout=(10, 30),
                catch_response=True,
            ) as response:
                if response.status_code != 200:
                    response.failure(f"HTTP {response.status_code}")
                    return sse.RunTiming(error=f"stream HTTP {response.status_code}")
                # Each chunk as it arrives: iter_lines would wait for 512 bytes.
                lines = sse.lines_of(response.iter_content(chunk_size=None))
                return sse.follow(
                    lines, started=started, deadline_s=STATE.settings.answer_timeout_s
                )
        except requests.RequestException as exc:
            return sse.RunTiming(error=f"stream broke: {type(exc).__name__}")

    def on_stop(self) -> None:
        for conversation in self.created:
            with self.client.delete(
                f"/api/v1/conversations/{conversation}",
                name="conversations: delete",
                catch_response=True,
            ) as response:
                if response.status_code not in (204, 404):  # 404: already gone
                    response.failure(f"HTTP {response.status_code}: {response.text[:200]}")
        self.created.clear()


class Asker(AskingUser):
    """Asks questions in the chat and reads the streamed answers; some are follow-ups in the
    same conversation (the question is rewritten first, one more model call)."""

    kind = "chat"

    @tag("model")
    @task
    def ask_question(self) -> None:
        follow_up = self.rng.random() < STATE.settings.follow_up
        self.ask(pick(self.questions("chat"), self.rng).text, "chat", follow_up=follow_up)


class AgentsAsker(AskingUser):
    """Questions that span departments (the planner and department agents run when the stack's
    ASSISTANT_MODE is agents or auto), and research-mode questions (a tool loop)."""

    kind = "agents"

    @tag("model")
    @task(3)
    def ask_question(self) -> None:
        self.ask(pick(self.questions("agents"), self.rng).text, "agents", follow_up=False)

    @tag("model")
    @task(1)
    def research(self) -> None:
        question = pick(self.questions("chat"), self.rng).text
        self.ask(question, "research", mode="research", follow_up=False)


class Analyst(NeedWiseUser):
    """The Insights page: business questions (model), the data model, past answers, and
    re-running an answer's SQL."""

    kind = "analyst"

    def candidates(self) -> list[str]:
        return STATE.bank.analysts(sorted(STATE.tokens))

    @tag("model")
    @task(1)
    def ask_insight(self) -> None:
        question = pick(STATE.bank.insights_for(self.email), self.rng)
        with self.client.post(
            "/api/v1/analytics/ask",
            json={"question": question.text},
            name="insights: ask",
            timeout=STATE.settings.answer_timeout_s,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")
                return
            body = response.json()
            if body.get("status") == "failed":
                response.failure(f"answer failed: {body.get('error')}")

    @task(2)
    def data_model(self) -> None:
        self.get_json("/api/v1/analytics/model", "insights: data model")

    @task(2)
    def past_answers(self) -> None:
        self.get_json("/api/v1/analytics/runs", "insights: past answers")

    @task(2)
    def rerun_query(self) -> None:
        question = pick(STATE.bank.insights_for(self.email), self.rng)
        with self.client.post(
            "/api/v1/analytics/query",
            json={"sql": question.sql},
            name="insights: re-run query",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")


class SessionUser(NeedWiseUser):
    """Signs in through Keycloak at the start and uses the browser session."""

    abstract = True
    uses_token = False

    def on_start(self) -> None:
        self.email = STATE.assign(self.kind, self.candidates())
        browser_like(self.client)
        try:
            sign_in(self.client, self.host or "", self.email, STATE.settings.password, names=True)
        except (SignInError, requests.RequestException) as exc:
            record(self.environment, "FLOW", "login: whole flow", None, str(exc))
            raise StopUser from exc

    def on_stop(self) -> None:
        sign_out(self.client, self.host or "", names=True)


class Admin(SessionUser):
    """An org admin on the admin pages."""

    kind = "admin"

    def candidates(self) -> list[str]:
        return [STATE.settings.admin]

    @task(2)
    def people(self) -> None:
        self.get_json("/api/v1/admin/users", "admin: people")

    @task(2)
    def audit_log(self) -> None:
        self.get_json("/api/v1/admin/audit-events", "admin: audit log", params={"limit": 50})

    @task(2)
    def usage_by_department(self) -> None:
        params = {**usage_range(), "group_by": "department", "interval": "week"}
        self.get_json("/api/v1/usage/summary", "admin: usage by department", params=params)

    @task(1)
    def models(self) -> None:
        self.get_json("/api/v1/admin/models", "admin: models")

    @task(1)
    def collections(self) -> None:
        self.get_json("/api/v1/admin/collections", "admin: collections")

    @task(1)
    def agents(self) -> None:
        self.get_json("/api/v1/admin/agents", "admin: agents")


class Login(NeedWiseUser):
    """Signs in through Keycloak, loads the profile, signs out: the login pages under load."""

    kind = "login"
    uses_token = False

    def candidates(self) -> list[str]:
        return list(DEMO_USERS)

    def on_start(self) -> None:
        self.email = STATE.assign(self.kind, self.candidates())
        browser_like(self.client)

    def wait_time(self) -> float:
        low, high = STATE.settings.think
        return self.rng.uniform(low * 3, high * 3)  # people sign in far less often

    @task
    def sign_in_and_out(self) -> None:
        started = time.perf_counter()
        try:
            sign_in(self.client, self.host or "", self.email, STATE.settings.password, names=True)
        except (SignInError, requests.RequestException) as exc:
            record(self.environment, "FLOW", "login: whole flow", None, str(exc))
            self.client.cookies.clear()
            return
        record(self.environment, "FLOW", "login: whole flow", time.perf_counter() - started)
        self.get_json("/api/v1/me", "me (session)")
        sign_out(self.client, self.host or "", names=True)


class Uploader(SessionUser):
    """Uploads a small Markdown document, waits until it is indexed, then deletes it."""

    kind = "upload"

    def __init__(self, environment: Environment) -> None:
        super().__init__(environment)
        self.collection = ""

    def candidates(self) -> list[str]:
        return [STATE.settings.uploader]

    def on_start(self) -> None:
        super().on_start()
        key = STATE.settings.upload_collection
        page = self.get_json("/api/v1/collections", "collections: list") or {"items": []}
        found = [c["id"] for c in page["items"] if c["key"] == key]
        if not found:
            record(self.environment, "FLOW", "upload: whole flow", None,
                   f"{self.email} cannot see a collection with key {key!r}")  # fmt: skip
            raise StopUser
        self.collection = found[0]

    def wait_time(self) -> float:
        low, high = STATE.settings.think
        return self.rng.uniform(low * 3, high * 3)

    @task
    def upload(self) -> None:
        filename = f"{UPLOAD_PREFIX}{uuid.uuid4().hex[:12]}.md"
        started = time.perf_counter()
        with self.client.post(
            f"/api/v1/collections/{self.collection}/documents",
            params={"filename": filename},
            data=document_text(filename).encode(),
            headers={"Content-Type": "text/markdown", **same_origin(self.host or "")},
            name="upload: send",
            catch_response=True,
        ) as response:
            if response.status_code != 202:
                response.failure(f"HTTP {response.status_code}: {response.text[:200]}")
                return
            document = response.json()["document"]["id"]
        status, deadline = "pending", started + STATE.settings.answer_timeout_s
        while status in ("pending", "processing") and time.perf_counter() < deadline:
            gevent.sleep(UPLOAD_POLL_S)
            body = self.get_json(f"/api/v1/documents/{document}", "upload: poll status")
            status = body["status"] if body else "failed"
        error = None if status == "ready" else f"status {status}"
        record(self.environment, "INGEST", "upload: time to ready", time.perf_counter() - started,
               error)  # fmt: skip
        self.client.delete(
            f"/api/v1/documents/{document}",
            headers=same_origin(self.host or ""),
            name="upload: delete",
        )


def document_text(filename: str) -> str:
    """About 2 KB of Markdown in a few sections, so it is parsed, chunked and embedded."""
    sections = [
        f"# Load test document {filename}\n\nWritten by the NeedWise load test and deleted again "
        "once it is indexed. It is not company information.",
    ]
    for number in range(1, 6):
        sections.append(
            f"## Section {number}\n\n"
            + "The warehouse robots follow the posted safety procedures during every shift. " * 6
        )
    return "\n\n".join(sections)
