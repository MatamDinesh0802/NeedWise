"""Checks a target before a load test: is it healthy, and which model would each call reach?

    uv run python -m needwise_load.preflight --host http://localhost:8480
    uv run python -m needwise_load.preflight --expect-model needwise-stub   # before model scenarios

Signs in as an org admin (erin@acme.test by default) and reads Admin > Models and Admin > Agents
(read-only; keys are never returned by the API). With --expect-model, it fails unless every
role, the data policy's local model and every agent with its own model resolve to that model on
the deployment's own `openai_compatible` provider: anything else would send load-test questions
to a real, paid model.
"""

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import requests

from needwise_load.browser import browser_like, sign_in, sign_out
from needwise_load.settings import (
    ADMIN_USER,
    DEFAULT_DEMO_PASSWORD,
    DEFAULT_HOST,
    DEMO_PASSWORD_ENV,
)

TIMEOUT = 30


@dataclass
class Findings:
    lines: list[str] = field(default_factory=list[str])
    problems: list[str] = field(default_factory=list[str])

    @property
    def ok(self) -> bool:
        return not self.problems


def system_status(host: str) -> dict[str, Any]:
    response = requests.get(urljoin(host, "/api/v1/system/status"), timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def check_models(catalog: dict[str, Any], agents: list[dict[str, Any]], expect: str) -> Findings:
    """Whether every model call of the tenant would reach `expect` on the system provider."""
    found = Findings()
    providers = {p["id"]: p for p in catalog.get("providers", [])}
    system = providers.get("system", {})
    found.lines.append(
        f"deployment provider: {system.get('kind')} {system.get('base_url') or ''}".rstrip()
    )
    wanted = f"system:{expect}"
    if system.get("kind") != "openai_compatible":
        found.problems.append(
            f"the deployment's provider is {system.get('kind')!r}, not the stub "
            "(set LLM_PROVIDER=openai_compatible and LLM_BASE_URL to the stub)"
        )
    for role, model in sorted(catalog.get("roles", {}).items()):
        found.lines.append(f"role {role}: {model}")
        if model != wanted:
            found.problems.append(f"the {role} role uses {model}, not {wanted}")
    local = (catalog.get("policy") or {}).get("local_model")
    found.lines.append(f"data policy local model: {local}")
    if local not in (None, wanted):
        found.problems.append(f"sensitive sources may go to {local} (Admin > Models > policy)")
    for agent in agents:
        if agent.get("model") and agent["model"] != wanted:
            found.problems.append(f"agent {agent['key']!r} has its own model {agent['model']}")
    return found


def inspect(host: str, admin: str, password: str, expect: str | None) -> Findings:
    status = system_status(host)
    found = Findings()
    found.lines.append(
        f"{status.get('product')} {status.get('version')} ({status.get('environment')}): "
        f"{status.get('status')}"
    )
    for component in status.get("components", []):
        found.lines.append(f"  {component['name']}: {component['status']}")
        if component["status"] != "ok":
            found.problems.append(f"{component['name']} is {component['status']}")
    session = browser_like(requests.Session())
    sign_in(session, host, admin, password, timeout=TIMEOUT)
    try:
        catalog = session.get(urljoin(host, "/api/v1/admin/models"), timeout=TIMEOUT)
        catalog.raise_for_status()
        agents = session.get(urljoin(host, "/api/v1/admin/agents"), timeout=TIMEOUT)
        agents.raise_for_status()
    finally:
        sign_out(session, host)
    items: list[dict[str, Any]] = agents.json().get("items", [])
    enabled = [a for a in items if a.get("enabled", True)]
    found.lines.append(f"agents enabled: {', '.join(a['key'] for a in enabled) or 'none'}")
    models = check_models(catalog.json(), enabled, expect or "")
    found.lines += models.lines
    if expect:
        found.problems += models.problems
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="needwise_load.preflight")
    parser.add_argument("--host", default=os.environ.get("NW_LOAD_HOST", DEFAULT_HOST))
    parser.add_argument("--admin", default=ADMIN_USER)
    parser.add_argument("--expect-model", help="fail unless every model call reaches this model")
    args = parser.parse_args(argv)
    password = os.environ.get(DEMO_PASSWORD_ENV, DEFAULT_DEMO_PASSWORD)
    try:
        found = inspect(args.host, args.admin, password, args.expect_model)
    except (requests.RequestException, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("\n".join(found.lines))
    for problem in found.problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    return 0 if found.ok else 1


if __name__ == "__main__":
    sys.exit(main())
