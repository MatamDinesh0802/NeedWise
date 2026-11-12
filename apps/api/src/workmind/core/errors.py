"""API errors as RFC 9457 problem details (application/problem+json)."""

import re
from collections.abc import Mapping
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        *,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        # More members of the problem document (e.g. "budget" for budget_exceeded).
        self.extra: dict[str, Any] = dict(extra or {})


def problem_response(error: ApiError) -> JSONResponse:
    content: dict[str, object] = {
        "type": "about:blank",
        "title": error.title,
        "status": error.status,
        "code": error.code,
    }
    if error.detail:
        content["detail"] = error.detail
    for key, value in error.extra.items():
        content.setdefault(key, value)
    return JSONResponse(
        status_code=error.status, content=content, media_type="application/problem+json"
    )


async def api_error_handler(_request: Request, exc: Exception) -> Response:
    if not isinstance(exc, ApiError):  # registered only for ApiError; kept total for typing
        raise exc
    return problem_response(exc)


# Fields whose values never go back in an error: Pydantic's errors carry the input, and for a
# missing field that input is the whole object, keys and passwords included.
SENSITIVE_FIELD = re.compile(
    r"(?:^|_)(?:api_?key|token|secret|password|passphrase|credentials?)$", re.IGNORECASE
)
REDACTED = "[redacted]"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: REDACTED if isinstance(k, str) and SENSITIVE_FIELD.search(k) else _redact(v)
            for k, v in value.items()  # pyright: ignore[reportUnknownVariableType]
        }
    if isinstance(value, list | tuple):
        return [_redact(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def redact_errors(errors: Any) -> list[dict[str, Any]]:
    redacted: list[dict[str, Any]] = []
    for error in errors:
        error = dict(error)
        loc = error.get("loc") or ()
        if any(isinstance(part, str) and SENSITIVE_FIELD.search(part) for part in loc):
            error["input"] = REDACTED
        elif "input" in error:
            error["input"] = _redact(error["input"])
        redacted.append(error)
    return redacted


async def validation_error_handler(_request: Request, exc: Exception) -> Response:
    """Request validation errors in the same problem+json shape as every other error."""
    if not isinstance(exc, RequestValidationError):
        raise exc
    return JSONResponse(
        status_code=422,
        content={
            "type": "about:blank",
            "title": "Invalid request",
            "status": 422,
            "code": "validation_error",
            "errors": jsonable_encoder(redact_errors(exc.errors())),
        },
        media_type="application/problem+json",
    )


def unauthenticated(detail: str = "Sign in to continue.") -> ApiError:
    return ApiError(401, "unauthenticated", "Not signed in", detail)
