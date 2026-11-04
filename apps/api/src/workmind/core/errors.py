"""API errors as RFC 9457 problem details (application/problem+json)."""

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response


class ApiError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str | None = None) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


def problem_response(error: ApiError) -> JSONResponse:
    content: dict[str, object] = {
        "type": "about:blank",
        "title": error.title,
        "status": error.status,
        "code": error.code,
    }
    if error.detail:
        content["detail"] = error.detail
    return JSONResponse(
        status_code=error.status, content=content, media_type="application/problem+json"
    )


async def api_error_handler(_request: Request, exc: Exception) -> Response:
    if not isinstance(exc, ApiError):  # registered only for ApiError; kept total for typing
        raise exc
    return problem_response(exc)


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
            "errors": jsonable_encoder(exc.errors()),
        },
        media_type="application/problem+json",
    )


def unauthenticated(detail: str = "Sign in to continue.") -> ApiError:
    return ApiError(401, "unauthenticated", "Not signed in", detail)
