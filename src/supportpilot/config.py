"""All configuration from environment variables (or a local `.env`), validated once at startup."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["fake", "openai", "azure", "bedrock"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Two roles, two keys. In a real shop the customer identity comes from a verified token issued by the shop's login;
    # here a trusted gateway sets X-Customer-Id after authenticating the customer, and this key proves it is the gateway.
    gateway_api_key: SecretStr = SecretStr("dev-gateway-key")
    admin_api_key: SecretStr = SecretStr("dev-admin-key")

    mongo_url: str = "mongodb://localhost:27018"
    mongo_db: str = "supportpilot"
    redis_url: str = "redis://localhost:6380/0"
    rate_limit_per_minute: int = 20

    llm_provider: Provider = "fake"
    # A second provider, used only when a call to the first one fails (outage, 5xx, timeout): same agent, other cloud.
    fallback_provider: Provider | Literal["none"] = "none"
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    chat_model: str = "gpt-4.1-mini"
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_chat_deployment: str | None = None
    # AWS Bedrock. No AWS secret is required here: boto3 looks for credentials itself (AWS_PROFILE / SSO on a laptop,
    # the IAM role of the container inside AWS). A Bedrock API key is the simplest option for local development.
    aws_region: str = "eu-central-1"
    bedrock_model_id: str | None = None
    aws_bearer_token_bedrock: SecretStr | None = None
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2

    # Tracing with OpenTelemetry: off by default, on in docker compose (Phoenix collects and shows the traces).
    tracing_enabled: bool = False
    otlp_endpoint: str = "http://localhost:6006/v1/traces"
    tracing_project: str = "supportpilot"

    # Guardrails: an agent is a loop, so it needs a ceiling on how many times it may go round.
    max_model_calls: int = 6
    max_tool_calls: int = 5

    orders_base_url: str = "http://localhost:9000"
    orders_api_key: SecretStr = SecretStr("dev-orders-key")
    docuask_base_url: str = "http://localhost:8000"
    docuask_api_key: SecretStr = SecretStr("dev-key")
    http_connect_timeout: float = 2.0
    http_read_timeout: float = 8.0
    http_retries: int = 3
    breaker_failures: int = 5
    breaker_reset_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
