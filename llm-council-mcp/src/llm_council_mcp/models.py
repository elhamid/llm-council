from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_MODELS = [
    "openai/gpt-5.2",
    "anthropic/claude-sonnet-4.5",
    "google/gemini-3-pro-preview",
    "x-ai/grok-4.1-fast",
]

DEFAULT_CHAIRMAN = "anthropic/claude-opus-4.5"


@dataclass
class CouncilConfig:
    stage1_models: list[str] = field(default_factory=list)
    stage2_models: list[str] = field(default_factory=list)
    chairman_model: str = ""
    timeout_seconds: int = 300
    max_tokens: int = 2048

    def __post_init__(self) -> None:
        if not self.stage1_models:
            env = os.getenv("COUNCIL_MODELS", "").strip()
            self.stage1_models = [m.strip() for m in env.split(",") if m.strip()] if env else list(DEFAULT_MODELS)
        if not self.stage2_models:
            self.stage2_models = list(self.stage1_models)
        if not self.chairman_model:
            self.chairman_model = (os.getenv("CHAIRMAN_MODEL") or "").strip() or DEFAULT_CHAIRMAN
        try:
            self.timeout_seconds = int((os.getenv("COUNCIL_TIMEOUT") or str(self.timeout_seconds)).strip())
        except ValueError:
            self.timeout_seconds = 300
        try:
            self.max_tokens = int((os.getenv("COUNCIL_MAX_TOKENS") or str(self.max_tokens)).strip())
        except ValueError:
            self.max_tokens = 2048


_CONFIG = CouncilConfig()


def get_config() -> CouncilConfig:
    return _CONFIG


def update_config(
    stage1_models: list[str] | None = None,
    stage2_models: list[str] | None = None,
    chairman_model: str | None = None,
    timeout_seconds: int | None = None,
) -> CouncilConfig:
    global _CONFIG

    if stage1_models is not None:
        _CONFIG.stage1_models = [m.strip() for m in stage1_models if m and m.strip()]
    if stage2_models is not None:
        _CONFIG.stage2_models = [m.strip() for m in stage2_models if m and m.strip()]
    if chairman_model is not None and chairman_model.strip():
        _CONFIG.chairman_model = chairman_model.strip()
    if timeout_seconds is not None:
        _CONFIG.timeout_seconds = int(timeout_seconds)
    return _CONFIG


def api_configured() -> bool:
    return bool((os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip())
