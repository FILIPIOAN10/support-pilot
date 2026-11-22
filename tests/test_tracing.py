"""Tracing: the spans of a real conversation turn, caught in memory instead of being sent to Phoenix."""

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from supportpilot.config import Settings
from supportpilot.tracing import setup_tracing, shutdown_tracing


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    provider = setup_tracing(Settings(_env_file=None, tracing_enabled=True), exporter=exporter)

    def finished():
        provider.force_flush()  # the batch processor exports in the background; wait for it
        return exporter.get_finished_spans()

    yield finished
    shutdown_tracing(provider)


def kind(span) -> str | None:
    return span.attributes.get("openinference.span.kind")


def test_a_turn_is_traced_with_its_model_calls_tool_calls_and_conversation(make_service, spans):
    service, _ = make_service()
    out = service.chat("c-100", "Where is my order 1002?")

    finished = spans()
    tools = [s for s in finished if kind(s) == "TOOL"]
    llms = [s for s in finished if kind(s) == "LLM"]
    assert [s.name for s in tools] == ["get_order"]
    assert len(llms) == 2  # one call decides to use the tool, the next one answers from its result
    assert all(s.attributes["llm.token_count.prompt"] > 0 for s in llms)
    assert {s.attributes.get("session.id") for s in finished} == {out.conversation_id}  # grouped per conversation
    assert {s.attributes.get("user.id") for s in finished} == {"c-100"}


def test_tracing_is_off_unless_enabled():
    assert setup_tracing(Settings(_env_file=None)) is None
