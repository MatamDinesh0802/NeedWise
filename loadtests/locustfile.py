"""NeedWise load test (docs/load-testing.md).

    uv run python -m needwise_load.tokens create
    uv run locust --host http://localhost:8480                      # web UI on :8089
    uv run locust --host http://localhost:8480 --headless -u 20 -r 2 -t 5m --nw-no-model
    uv run python -m needwise_load.tokens revoke

`make loadtest` does all of it, then writes a report (needwise_load/run.py).
"""

from typing import Any

from locust import events
from locust.env import Environment

from needwise_load.users import (
    Admin,
    AgentsAsker,
    Analyst,
    Asker,
    Login,
    Poller,
    Reader,
    Uploader,
    add_options,
    configure,
)

__all__ = ["Admin", "AgentsAsker", "Analyst", "Asker", "Login", "Poller", "Reader", "Uploader"]


@events.init_command_line_parser.add_listener
def _options(parser: Any) -> None:
    add_options(parser)


@events.init.add_listener
def _configure(environment: Environment, **_: Any) -> None:
    configure(environment)
