"""Workflow definitions written as YAML (or JSON, which is YAML too): parsed, validated, and
every problem reported at its line, for Admin > Workflows' editor and the API.

Safe by construction: PyYAML's safe loader (no Python objects), a size limit, and no aliases
(`*name`), which would let a short document expand into a huge one when it is stored as JSON.
YAML's dates become ISO strings, so the stored definition is plain JSON.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, cast

import yaml
from pydantic import BaseModel, ValidationError

MAX_SOURCE: Final = 100_000


@dataclass(frozen=True)
class SourceProblem:
    line: int | None  # 1-based
    column: int | None  # 1-based
    path: str  # where in the definition, e.g. "steps[1].tool"
    message: str

    def as_json(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "column": self.column,
            "path": self.path,
            "message": self.message,
        }


def _plain(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, list):
        return [_plain(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def _path(loc: Sequence[int | str]) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "(definition)"


def _node_at(root: yaml.Node | None, loc: Sequence[int | str]) -> yaml.Node | None:
    """The YAML node a validation error points at, or the closest one that exists."""
    node = root
    for part in loc:
        if isinstance(node, yaml.MappingNode) and isinstance(part, str):
            found = next(
                (v for k, v in node.value if isinstance(k, yaml.ScalarNode) and k.value == part),
                None,
            )
        elif isinstance(node, yaml.SequenceNode) and isinstance(part, int):
            found = node.value[part] if 0 <= part < len(node.value) else None
        else:
            found = None
        if found is None:
            return node
        node = found
    return node


def _message(error: Any) -> str:
    message = str(error.get("msg", "invalid"))
    for prefix in ("Value error, ", "Assertion failed, "):
        message = message.removeprefix(prefix)
    return message


def parse_source[T: BaseModel](text: str, model: type[T]) -> tuple[T | None, list[SourceProblem]]:
    """The validated definition, or the problems (each at its line when it has one)."""
    if len(text) > MAX_SOURCE:
        return None, [SourceProblem(None, None, "(definition)", "The definition is too long")]
    try:
        events = cast(Iterable[yaml.Event], yaml.parse(text, Loader=yaml.SafeLoader))  # pyright: ignore[reportUnknownMemberType]
        for event in events:
            if isinstance(event, yaml.AliasEvent):
                mark = event.start_mark
                return None, [
                    SourceProblem(
                        mark.line + 1 if mark else None,
                        mark.column + 1 if mark else None,
                        "(definition)",
                        "Aliases (*name) are not supported: write the value out",
                    )
                ]
        root = cast(yaml.Node | None, yaml.compose(text, Loader=yaml.SafeLoader))  # pyright: ignore[reportUnknownMemberType]
        data = yaml.safe_load(text)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        reason = exc.problem or exc.context or "not valid YAML"
        return None, [
            SourceProblem(
                mark.line + 1 if mark else None,
                mark.column + 1 if mark else None,
                "(definition)",
                f"Not valid YAML: {reason}",
            )
        ]
    except yaml.YAMLError as exc:
        return None, [SourceProblem(None, None, "(definition)", f"Not valid YAML: {exc}")]
    if not isinstance(data, dict):
        return None, [
            SourceProblem(
                1, 1, "(definition)", "The definition must be a mapping (trigger:, steps:)"
            )
        ]
    try:
        return model.model_validate(_plain(data)), []
    except ValidationError as exc:
        problems: list[SourceProblem] = []
        for error in exc.errors(include_url=False, include_input=False):
            loc = tuple(error["loc"])
            node = _node_at(root, loc)
            mark = node.start_mark if node is not None else None
            problems.append(
                SourceProblem(
                    mark.line + 1 if mark else None,
                    mark.column + 1 if mark else None,
                    _path(loc),
                    _message(error),
                )
            )
        return None, problems


def to_yaml(definition: dict[str, Any]) -> str:
    """A stored definition as YAML, for one saved as JSON (no comments to keep)."""
    return yaml.safe_dump(
        definition, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100
    )
