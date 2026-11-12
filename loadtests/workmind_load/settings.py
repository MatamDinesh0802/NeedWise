"""What a load-test run is configured with.

Locust options (`--wm-*`) each have an environment variable (`WM_LOAD_*`), so the same
settings work headless (`make loadtest`), in Locust's web UI and in CI.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

LOADTESTS_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = LOADTESTS_DIR.parent
DATASETS_DIR = REPO_DIR / "evals" / "datasets"
RESULTS_DIR = LOADTESTS_DIR / "results"
# Plain tokens live here between `tokens create` and `tokens revoke` (git-ignored, mode 0600).
TOKEN_FILE = LOADTESTS_DIR / ".secrets" / "tokens.json"

DEFAULT_HOST = "http://localhost:8480"
DEMO_PASSWORD_ENV = "DEMO_PASSWORD"  # noqa: S105  (the variable's name)
# The seeded demo logins' published password (seed/acme, docs): demo data only.
DEFAULT_DEMO_PASSWORD = "workmind-demo"  # noqa: S105
# The seeded logins (seed/acme/company.yaml, `demo: true`).
DEMO_USERS = (
    "alice@acme.test",
    "bob@acme.test",
    "carol@acme.test",
    "dan@acme.test",
    "erin@acme.test",
    "mallory@acme.test",
    "priya.raman@acme.test",
    "hannah.schmidt@acme.test",
    "olivia.grant@acme.test",
)
ADMIN_USER = "erin@acme.test"  # org admin: the admin scenario and preflight sign in as erin

# Names that let `tokens sweep` find what a run left behind.
TOKEN_NAME_PREFIX = "loadtest-"  # noqa: S105  (a token's display name, not a secret)
CONVERSATION_PREFIX = "[load] "
UPLOAD_PREFIX = "loadtest-upload-"

# Relative weights of the user classes (users.py). Locust spreads -u users over them.
DEFAULT_MIX = {
    "reader": 10,  # browses conversations, collections, documents; searches
    "poller": 6,  # an open tab: notifications and approvals every 30 s
    "chat": 3,  # asks questions and streams the answers (model)
    "agents": 1,  # cross-department questions and research mode (model)
    "analyst": 2,  # insights questions (model), data model, past answers, re-run SQL
    "admin": 1,  # admin pages with a browser session
    "login": 1,  # signs in through Keycloak and out again
    "upload": 1,  # uploads a document, waits until it is indexed, deletes it
}
MODEL_CLASSES = frozenset({"chat", "agents"})  # skipped entirely with --wm-no-model


class SettingsError(ValueError):
    pass


def parse_mix(text: str) -> dict[str, int]:
    """'reader=10,chat=3' -> weights; classes left out keep their default weight."""
    mix = dict(DEFAULT_MIX)
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        name, separator, value = part.partition("=")
        name = name.strip()
        if not separator or name not in DEFAULT_MIX:
            raise SettingsError(f"unknown mix entry {part!r}: use {', '.join(DEFAULT_MIX)}")
        try:
            weight = int(value)
        except ValueError as exc:
            raise SettingsError(f"the weight of {name!r} must be a whole number") from exc
        if weight < 0:
            raise SettingsError(f"the weight of {name!r} cannot be negative")
        mix[name] = weight
    return mix


def parse_range(text: str) -> tuple[float, float]:
    """'3,10' -> (3.0, 10.0): think time between a person's actions, in seconds."""
    low, _, high = text.partition(",")
    try:
        bounds = (float(low), float(high or low))
    except ValueError as exc:
        raise SettingsError(f"expected 'min,max' seconds, got {text!r}") from exc
    if bounds[0] < 0 or bounds[1] < bounds[0]:
        raise SettingsError(f"expected 0 <= min <= max, got {text!r}")
    return bounds


@dataclass(frozen=True)
class Settings:
    tokens: Path = TOKEN_FILE
    mix: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_MIX))
    no_model: bool = False
    think: tuple[float, float] = (3.0, 10.0)
    datasets: Path = DATASETS_DIR
    answer_timeout_s: float = 180.0
    follow_up: float = 0.3  # share of chat questions asked as a follow-up in the same thread
    upload_collection: str | None = None  # a collection key; unset: no uploads
    uploader: str = ADMIN_USER
    admin: str = ADMIN_USER
    password: str = field(default=DEFAULT_DEMO_PASSWORD, repr=False)

    @property
    def weights(self) -> dict[str, int]:
        """The effective weights: model classes are off without a model, uploads without a
        collection to upload to."""
        weights = dict(self.mix)
        if self.no_model:
            weights.update({name: 0 for name in MODEL_CLASSES})
        if not self.upload_collection:
            weights["upload"] = 0
        return weights


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)
