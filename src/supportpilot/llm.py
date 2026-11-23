"""The chat model, built from settings in one place: fake (offline) / OpenAI / Azure OpenAI / AWS Bedrock."""

from langchain_aws import ChatBedrockConverse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from supportpilot.config import Provider, Settings
from supportpilot.fake import ScriptedDraftModel, ScriptedSupportModel


class ProviderConfigError(RuntimeError):
    """A setting the chosen provider needs is missing (raised at startup, so the pod fails fast)."""


def _need(value, name: str):
    if not value:
        raise ProviderConfigError(f"{name} is required for this LLM_PROVIDER")
    return value.get_secret_value() if hasattr(value, "get_secret_value") else value


def make_chat_model(s: Settings, provider: Provider | None = None) -> BaseChatModel:
    provider = provider or s.llm_provider
    if provider == "fake":
        return ScriptedSupportModel()
    if provider == "openai":
        return ChatOpenAI(
            model=s.chat_model,
            api_key=_need(s.openai_api_key, "OPENAI_API_KEY"),
            base_url=s.openai_base_url,
            timeout=s.llm_timeout_seconds,
            max_retries=s.llm_max_retries,
        )
    if provider == "azure":
        return AzureChatOpenAI(
            azure_deployment=_need(s.azure_chat_deployment, "AZURE_CHAT_DEPLOYMENT"),
            azure_endpoint=_need(s.azure_openai_endpoint, "AZURE_OPENAI_ENDPOINT"),
            api_key=_need(s.azure_openai_api_key, "AZURE_OPENAI_API_KEY"),
            api_version=s.azure_openai_api_version,
            timeout=s.llm_timeout_seconds,
            max_retries=s.llm_max_retries,
        )
    if provider == "bedrock":
        # The Converse API: one request shape for every Bedrock model (Amazon Nova, Anthropic Claude, Mistral...),
        # tool calling included - so switching the model is a configuration change, not a code change.
        return ChatBedrockConverse(
            model=_need(s.bedrock_model_id, "BEDROCK_MODEL_ID"),
            region_name=s.aws_region,
            api_key=s.aws_bearer_token_bedrock,  # None -> the normal AWS credential chain
            max_tokens=1024,
            timeout=int(s.llm_timeout_seconds),
            max_retries=s.llm_max_retries,
        )
    raise ProviderConfigError(f"unknown LLM provider: {provider}")


def make_fallback_model(s: Settings) -> BaseChatModel | None:
    """The model to try when the primary one fails, or None when no fallback is configured."""
    if s.fallback_provider in ("none", s.llm_provider):
        return None
    return make_chat_model(s, s.fallback_provider)


def make_draft_model(s: Settings):
    """The model behind the colleague's draft: the same provider as the agent (and its fallback), without tools."""
    if s.llm_provider == "fake":
        return ScriptedDraftModel()
    model = make_chat_model(s)
    fallback = make_fallback_model(s)
    return model.with_fallbacks([fallback]) if fallback is not None else model
