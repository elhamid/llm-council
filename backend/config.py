"""Configuration for the LLM Council."""

import os

# Optional .env support (doesn't hard-require python-dotenv)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

# OpenRouter API key
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Council members - list of OpenRouter model identifiers.
# Override via env var COUNCIL_MODELS as a comma-separated list.
_council_models_env = os.getenv("COUNCIL_MODELS", "").strip()
COUNCIL_MODELS = (
    [m.strip() for m in _council_models_env.split(",") if m.strip()]
    if _council_models_env
    else [
        "openai/gpt-5.2",
        "google/gemini-3-pro-preview",
        "anthropic/claude-sonnet-4.5",
        "x-ai/grok-4.1-fast",
    ]
)

# Chairman model - synthesizes final response.
# Override via env var CHAIRMAN_MODEL.
CHAIRMAN_MODEL = os.getenv("CHAIRMAN_MODEL", "anthropic/claude-opus-4.5")

# Contract stack (comma-separated). Always include the factory base contract.
# Example: COUNCIL_CONTRACTS=factory_truth_v1,eldercare_safety_v1
COUNCIL_CONTRACTS = os.getenv("COUNCIL_CONTRACTS", "factory_truth_v1").strip()

# OpenRouter API endpoint
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Data directory for conversation storage
DATA_DIR = "data/conversations"