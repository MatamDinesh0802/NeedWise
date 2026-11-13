"""OpenTelemetry tracing, enabled only when an OTLP endpoint is configured.

Uses the standard OTEL_* environment variables (OTEL_EXPORTER_OTLP_ENDPOINT,
OTEL_SERVICE_NAME, ...), so any OTLP-compatible backend works: Langfuse, Jaeger, Tempo.
"""

import os

from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider

from needwise.core.config import Settings


def configure_telemetry(app: FastAPI, settings: Settings) -> TracerProvider | None:
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return None

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create(
        {
            "service.name": os.environ.get("OTEL_SERVICE_NAME", "needwise-api"),
            "service.version": settings.version,
            "deployment.environment.name": settings.app_env,
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")
    return provider
