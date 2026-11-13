"""Tracing and log correlation (core/telemetry.py, core/logging.py): off without an OTLP
endpoint; with one, the FastAPI app is instrumented and spans carry the service's name and
version; log lines carry the active span's trace and span ids."""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from needwise.core.config import Settings
from needwise.core.logging import add_trace_ids
from needwise.core.telemetry import configure_telemetry


def settings() -> Settings:
    return Settings(_env_file=None, app_env="test")  # pyright: ignore[reportCallIssue]


def test_tracing_is_off_without_an_otlp_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert configure_telemetry(FastAPI(), settings()) is None


def test_with_an_endpoint_requests_become_spans_of_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")  # never reached
    monkeypatch.setenv("OTEL_SERVICE_NAME", "needwise-api-test")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "1")
    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"ok": "yes"}

    provider = configure_telemetry(app, settings())
    assert isinstance(provider, TracerProvider)
    exported = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exported))
    try:
        with TestClient(app) as client:
            assert client.get("/ping").status_code == 200
        spans = exported.get_finished_spans()
        assert spans, "the request made no span"
        resource: dict[str, Any] = dict(spans[0].resource.attributes)
        assert resource["service.name"] == "needwise-api-test"
        assert resource["deployment.environment.name"] == "test"
        assert any(s.attributes and s.attributes.get("http.route") == "/ping" for s in spans)
    finally:
        provider.shutdown()


def test_log_lines_carry_the_active_spans_ids() -> None:
    tracer = TracerProvider().get_tracer("test")
    assert add_trace_ids(None, "info", {"event": "outside"}) == {"event": "outside"}
    with tracer.start_as_current_span("work") as span:
        context = span.get_span_context()
        event = add_trace_ids(None, "info", {"event": "inside"})
    assert event["trace_id"] == format(context.trace_id, "032x")
    assert event["span_id"] == format(context.span_id, "016x")
