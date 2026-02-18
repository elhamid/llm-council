from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_openai_response():
    """Create a mock OpenAI chat completion response."""

    def _make(content: str):
        msg = MagicMock()
        msg.content = content
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    return _make


@pytest.fixture
def sample_stage1_results():
    return [
        {"model": "openai/gpt-5.2", "response": "Answer from GPT-5.2 about architecture..."},
        {"model": "anthropic/claude-sonnet-4.5", "response": "Answer from Claude about architecture..."},
        {"model": "google/gemini-3-pro-preview", "response": "Answer from Gemini about architecture..."},
        {"model": "x-ai/grok-4.1-fast", "response": "Answer from Grok about architecture..."},
    ]


@pytest.fixture
def sample_stage2_results():
    return [
        {
            "model": "openai/gpt-5.2",
            "ranking": "Response A: Strength: ...; Flaw: ...\nResponse B: ...\nResponse C: ...\nResponse D: ...\nFINAL_RANKING: Response B > Response A > Response C > Response D",
            "parsed_ranking": ["Response B", "Response A", "Response C", "Response D"],
            "partial": False,
        }
    ]


@pytest.fixture
def tmp_db(tmp_path):
    return tmp_path / "test_deliberations.db"
