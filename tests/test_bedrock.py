"""AWS Bedrock without an AWS account: the real ChatBedrockConverse client + our tools + the agent loop, with boto3's
own Stubber answering instead of AWS. Plus the fallback: when the first provider fails, the second one answers."""

import copy

import boto3
import pytest
from botocore.stub import Stubber
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage
from langchain_core.outputs import ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from supportpilot.agent import build_agent
from supportpilot.config import Settings
from supportpilot.fake import ScriptedSupportModel
from supportpilot.llm import ProviderConfigError, make_chat_model, make_fallback_model

CFG = {"configurable": {"thread_id": "t", "customer_id": "c-100"}}


def converse_response(content: list[dict], stop_reason: str) -> dict:
    """The shape of a Bedrock Converse API response (boto3 validates it against the real API model)."""
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop_reason,
        "usage": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
        "metrics": {"latencyMs": 42},
    }


@pytest.fixture
def bedrock():
    """A real boto3 bedrock-runtime client whose answers come from a Stubber, plus a record of what was sent."""
    client = boto3.client(
        "bedrock-runtime",
        region_name="eu-central-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    sent: list[dict] = []
    client.meta.events.register(
        "provide-client-params.bedrock-runtime.Converse",
        lambda params, **_: sent.append(copy.deepcopy(params)),
    )
    stubber = Stubber(client)
    tool_use = {"toolUse": {"toolUseId": "tooluse_1", "name": "get_order", "input": {"order_id": "1002"}}}
    stubber.add_response("converse", converse_response([tool_use], "tool_use"))
    stubber.add_response("converse", converse_response([{"text": "Your order 1002 has shipped."}], "end_turn"))
    with stubber:
        yield client, sent
    stubber.assert_no_pending_responses()  # both answers were used: exactly two model calls


def test_bedrock_drives_the_agent_through_a_tool_call(bedrock, tools, settings):
    client, sent = bedrock
    model = ChatBedrockConverse(model="test-model", region_name="eu-central-1", client=client)
    agent = build_agent(model, tools, InMemorySaver(), settings)

    result = agent.invoke({"messages": [HumanMessage("Where is my order 1002?")]}, CFG, version="v2")

    assert result.value["messages"][-1].content == "Your order 1002 has shipped."
    first, second = sent
    specs = {t["toolSpec"]["name"]: t["toolSpec"]["inputSchema"]["json"] for t in first["toolConfig"]["tools"]}
    assert set(specs) == {
        "get_order",
        "search_policies",
        "check_refund_eligibility",
        "request_refund",
        "handoff_to_human",
    }
    assert list(specs["get_order"]["properties"]) == ["order_id"]  # the model is never offered customer_id
    tool_result = second["messages"][-1]["content"][0]["toolResult"]
    assert tool_result["toolUseId"] == "tooluse_1" and "1002" in str(tool_result["content"])


def test_bedrock_settings_become_a_converse_client():
    s = Settings(
        _env_file=None, llm_provider="bedrock", bedrock_model_id="eu.amazon.nova-lite-v1:0", aws_region="eu-west-1"
    )
    model = make_chat_model(s)
    assert isinstance(model, ChatBedrockConverse)
    assert model.model_id == "eu.amazon.nova-lite-v1:0" and model.region_name == "eu-west-1"


def test_bedrock_needs_a_model_id():
    with pytest.raises(ProviderConfigError, match="BEDROCK_MODEL_ID"):
        make_chat_model(Settings(_env_file=None, llm_provider="bedrock"))


class Down(ScriptedSupportModel):
    """The primary provider during an outage."""

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        raise ConnectionError("provider is down")


def test_when_the_first_provider_fails_the_fallback_answers(tools, settings):
    agent = build_agent(Down(), tools, InMemorySaver(), settings, fallback=ScriptedSupportModel())
    result = agent.invoke({"messages": [HumanMessage("Where is my order 1002?")]}, CFG, version="v2")
    assert "shipped" in result.value["messages"][-1].content


def test_without_a_fallback_the_error_reaches_the_caller(tools, settings):
    agent = build_agent(Down(), tools, InMemorySaver(), settings)
    with pytest.raises(ConnectionError):
        agent.invoke({"messages": [HumanMessage("Where is my order 1002?")]}, CFG, version="v2")


def test_no_fallback_unless_configured_and_never_the_same_provider_twice():
    assert make_fallback_model(Settings(_env_file=None)) is None
    assert make_fallback_model(Settings(_env_file=None, llm_provider="fake", fallback_provider="fake")) is None
