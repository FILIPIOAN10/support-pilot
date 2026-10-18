"""The real ChatOpenAI client + our tools + the agent loop, against a local fake OpenAI server."""

import json

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from supportpilot import config
from supportpilot.agent import build_agent
from supportpilot.llm import ProviderConfigError, make_chat_model
from tests import fake_openai
from tests.servers import RunningApp


@pytest.fixture(scope="module")
def server():
    with RunningApp(fake_openai.app) as s:
        yield s


def test_a_real_chat_client_drives_the_agent_through_a_tool_call(server, tools, settings):
    fake_openai.requests_seen.clear()
    s = config.Settings(
        _env_file=None,
        llm_provider="openai",
        openai_api_key="sk-test",
        openai_base_url=server.url + "/v1",
        chat_model="test-model",
        llm_max_retries=0,
    )
    agent = build_agent(make_chat_model(s), tools, InMemorySaver(), settings)
    cfg = {"configurable": {"thread_id": "t", "customer_id": "c-100"}}
    result = agent.invoke({"messages": [HumanMessage("Where is my order 1002?")]}, cfg, version="v2")

    final = result.value["messages"][-1].content
    assert "The system says:" in final and "shipped" in final
    first, second = fake_openai.requests_seen[0], fake_openai.requests_seen[1]
    assert first["model"] == "test-model" and first["messages"][0]["role"] == "system"
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in first["tools"]}
    assert set(schemas) == {
        "get_order",
        "search_policies",
        "check_refund_eligibility",
        "request_refund",
    }
    assert list(schemas["get_order"]["properties"]) == ["order_id"]  # the model is never offered customer_id or config
    assert [m["role"] for m in second["messages"]][-2:] == [
        "assistant",
        "tool",
    ]  # the tool result went back
    assert json.loads(second["messages"][-1]["content"])["id"] == "1002"


def test_openai_provider_needs_its_key():
    with pytest.raises(ProviderConfigError, match="OPENAI_API_KEY"):
        make_chat_model(config.Settings(_env_file=None, llm_provider="openai"))
