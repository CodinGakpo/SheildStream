import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_provider: TracerProvider | None = None


def setup_tracing() -> None:
    global _provider
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger:4317")
    resource = Resource(attributes={SERVICE_NAME: "shieldstream.gateway"})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _provider = provider


def shutdown_tracing() -> None:
    # Flush any buffered spans before the process exits — BatchSpanProcessor
    # exports periodically, not immediately, so an unflushed shutdown silently
    # drops the last batch (Week 10 pitfall, applied here from the start).
    if _provider is not None:
        _provider.shutdown()


tracer = trace.get_tracer("shieldstream.gateway")
