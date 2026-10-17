"""The chat model, built from settings in one place: fake (offline) / OpenAI / Azure OpenAI."""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from supportpilot.config import Settings
from supportpilot.fake import ScriptedSupportModel


class ProviderConfigError(RuntimeError):
    """A setting the chosen provider needs is missing (raised at startup, so the pod fails fast)."""


def _need(value, name: str):
    if not value:
        raise ProviderConfigError(f"{name} is required for this LLM_PROVIDER")
    return value.get_secret_value() if hasattr(value, "get_secret_value") else value


def make_chat_model(s: Settings) -> BaseChatModel:
    if s.llm_provider == "fake":
        return ScriptedSupportModel()
    if s.llm_provider == "openai":
        return ChatOpenAI(
            model=s.chat_model,
            api_key=_need(s.openai_api_key, "OPENAI_API_KEY"),
            base_url=s.openai_base_url,
            timeout=s.llm_timeout_seconds,
            max_retries=s.llm_max_retries,
        )
    return AzureChatOpenAI(
        azure_deployment=_need(s.azure_chat_deployment, "AZURE_CHAT_DEPLOYMENT"),
        azure_endpoint=_need(s.azure_openai_endpoint, "AZURE_OPENAI_ENDPOINT"),
        api_key=_need(s.azure_openai_api_key, "AZURE_OPENAI_API_KEY"),
        api_version=s.azure_openai_api_version,
        timeout=s.llm_timeout_seconds,
        max_retries=s.llm_max_retries,
    )
