"""
Configuration for action_planner microservice.

All settings are read from environment variables with sensible defaults.
"""
import os


class Settings:
    """Centralized configuration for the action_planner service."""

    # gRPC server
    GRPC_PORT = int(os.getenv("GRPC_PORT", "5090"))
    GRPC_MAX_WORKERS = int(os.getenv("GRPC_MAX_WORKERS", "10"))

    # OpenAI API
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    # Token limits per LLM call type
    MAX_TOKENS_CLASSIFY = 200
    MAX_TOKENS_PARSE = 1000
    MAX_TOKENS_CONVO = 300

    # Temperature per LLM call type
    TEMPERATURE_CLASSIFY = 0.1
    TEMPERATURE_PARSE = 0.1
    TEMPERATURE_CONVO = 0.7

    # Timeouts (seconds)
    LLM_TIMEOUT_CLASSIFY = 10.0
    LLM_TIMEOUT_PARSE = 15.0
    LLM_TIMEOUT_CONVO = 15.0

    # Confidence thresholds
    MIN_CONFIDENCE_LLM = 0.6  # Below this, fall back to keyword matching

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # Service metadata
    SERVICE_NAME = "action_planner"
    SERVICE_VERSION = "2.0.0"


settings = Settings()
