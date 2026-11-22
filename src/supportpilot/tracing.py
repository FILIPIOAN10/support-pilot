"""Tracing: every agent run becomes a tree of spans - the run, each model call (messages, reply, tokens, duration),
each tool call (arguments, result, duration) - exported with OpenTelemetry (OTLP/HTTP).

Locally the traces go to Arize Phoenix (one container, UI on http://localhost:6006). OTLP is an open standard, so the
same code can send them to Langfuse, Jaeger, Grafana Tempo, Datadog or AWS X-Ray by changing one URL.
OpenInference is the part that knows LangChain: it turns LangChain/LangGraph callbacks into spans with LLM attributes.

Privacy: spans contain what the customer wrote. Emails are redacted before the model ever sees them (PIIMiddleware), and
OPENINFERENCE_HIDE_INPUTS=true / OPENINFERENCE_HIDE_OUTPUTS=true drop the texts entirely, keeping timings and tokens.
"""

import logging

from openinference.instrumentation.langchain import LangChainInstrumentor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from supportpilot.config import Settings

log = logging.getLogger(__name__)


def setup_tracing(settings: Settings, exporter: SpanExporter | None = None) -> TracerProvider | None:
    """Turn tracing on (if configured). Returns the provider, which must be shut down to flush the last spans."""
    if not settings.tracing_enabled:
        return None
    resource = Resource.create({"service.name": "supportpilot", "openinference.project.name": settings.tracing_project})
    provider = TracerProvider(resource=resource)
    # batched and in a background thread: exporting never slows down a customer's request
    provider.add_span_processor(BatchSpanProcessor(exporter or OTLPSpanExporter(endpoint=settings.otlp_endpoint)))
    LangChainInstrumentor().instrument(tracer_provider=provider)
    log.info("tracing on, exporting to %s", settings.otlp_endpoint)
    return provider


def shutdown_tracing(provider: TracerProvider | None) -> None:
    if provider is None:
        return
    LangChainInstrumentor().uninstrument()
    provider.shutdown()  # sends the spans still waiting in the batch
