from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import llm_council_mcp.council as council
from llm_council_mcp.contracts import (
    build_chairman_contract_system_messages,
    build_contract_system_messages,
    evaluate_contract_compliance,
    parse_contract_ids,
)


def test_parse_ranking_standard():
    t = "FINAL_RANKING: Response B > Response A > Response C > Response D"
    assert council._parse_ranking_from_text(t) == ["Response B", "Response A", "Response C", "Response D"]


def test_parse_ranking_arrow_variants():
    t = "FINAL_RANKING: Response B -> Response A -> Response C -> Response D"
    assert council._parse_ranking_from_text(t) == ["Response B", "Response A", "Response C", "Response D"]


def test_parse_ranking_letter_only():
    t = "FINAL_RANKING: B > A > C > D"
    assert council._parse_ranking_from_text(t) == ["Response B", "Response A", "Response C", "Response D"]


def test_parse_ranking_letter_only_extended():
    t = "FINAL_RANKING: E > C > A > B > D"
    parsed = council._parse_ranking_from_text(t)
    assert parsed == ["Response E", "Response C", "Response A", "Response B", "Response D"]


def test_parse_ranking_missing_labels_returns_empty():
    t = "FINAL_RANKING: B > A"
    assert council._parse_ranking_order(t, allowed_labels=["Response A", "Response B", "Response C", "Response D"]) == []


def test_parse_ranking_extra_text():
    t = "something\nFINAL_RANKING: Response A > Response B > Response C > Response D\nthanks"
    assert council._parse_ranking_from_text(t) == ["Response A", "Response B", "Response C", "Response D"]


def test_parse_fuzzy_chain():
    t = "I pick Response C > Response A > Response D > Response B"
    assert council._parse_ranking_from_text(t) == ["Response C", "Response A", "Response D", "Response B"]


def test_coerce_stage2_5line_partial_output():
    labels = ["Response A", "Response B", "Response C", "Response D"]
    raw = "Response A: Good\nResponse B: Better\nFINAL_RANKING: Response B > Response A > Response C > Response D"
    out = council._coerce_stage2_5line(raw, labels)
    assert out.count("\n") == 4
    assert "FINAL_RANKING" in out


def test_coerce_stage2_generalized_line_counts():
    labels3 = ["Response A", "Response B", "Response C"]
    raw3 = "Response A: x\nResponse B: y\nFINAL_RANKING: Response B > Response A > Response C"
    out3 = council._coerce_stage2_5line(raw3, labels3)
    lines3 = [ln for ln in out3.splitlines() if ln.strip()]
    assert len(lines3) == 4
    assert lines3[-1].startswith("FINAL_RANKING:")

    labels5 = ["Response A", "Response B", "Response C", "Response D", "Response E"]
    raw5 = "FINAL_RANKING: Response C > Response A > Response E > Response B > Response D"
    out5 = council._coerce_stage2_5line(raw5, labels5)
    lines5 = [ln for ln in out5.splitlines() if ln.strip()]
    assert len(lines5) == 6
    assert lines5[-1].endswith("Response C > Response A > Response E > Response B > Response D")


def test_label_responses(sample_stage1_results):
    labeled, mapping = council._label_responses(sample_stage1_results)
    assert len(labeled) == 4
    assert mapping["Response A"] == "openai/gpt-5.2"


def test_content_to_text_variants():
    assert council._content_to_text("hello") == "hello"
    assert council._content_to_text([{"text": "a"}, {"content": "b"}]) == "ab"
    assert council._content_to_text({"text": {"value": "x"}}) == "x"


def test_looks_like_provider_id_filtering():
    assert council._looks_like_provider_id("chatcmpl-123456789012")
    assert not council._looks_like_provider_id("normal response text")


def test_calculate_aggregate_rankings():
    stage2 = [
        {"parsed_ranking": ["Response A", "Response B"], "partial": False, "synthetic": False},
        {"parsed_ranking": ["Response A", "Response B"], "partial": False, "synthetic": False},
        {"parsed_ranking": ["Response B", "Response A"], "partial": True, "synthetic": False},
    ]
    label_to_model = {"Response A": "m1", "Response B": "m2"}
    agg = council.calculate_aggregate_rankings(stage2, label_to_model, contract_evals_by_model={"m2": {"eligible": False, "hard_fail_reasons": ["x"]}})
    assert agg[0]["model"] == "m1"


@pytest.mark.asyncio
async def test_run_deliberation_end_to_end(monkeypatch):
    msg = SimpleNamespace(content="ok")
    response = SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    create = AsyncMock(return_value=response)
    mock_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(council, "_client", lambda: mock_client)

    result = await council.run_deliberation(
        prompt="microservices vs monolith",
        stage1_models=["openai/gpt-5.2", "anthropic/claude-sonnet-4.5", "google/gemini-3-pro-preview", "x-ai/grok-4.1-fast"],
        stage2_models=["openai/gpt-5.2", "anthropic/claude-sonnet-4.5", "google/gemini-3-pro-preview", "x-ai/grok-4.1-fast"],
        chairman_model="anthropic/claude-opus-4.5",
        depth="quick",
        timeout=120,
    )
    assert "answer" in result
    assert "stage1_responses" in result
    assert "stage2_rankings" in result
    assert "aggregate_rankings" in result


def test_parse_contract_ids_no_auto_inject():
    assert parse_contract_ids(None) == []
    assert parse_contract_ids("factory_truth_v1") == ["factory_truth_v1"]
    assert parse_contract_ids("factory_truth_v1,factory_truth_v1") == ["factory_truth_v1"]


def test_build_contract_messages():
    msgs = build_contract_system_messages("factory_truth_v1")
    assert msgs and msgs[0]["role"] == "system"
    cmsgs = build_chairman_contract_system_messages("factory_truth_v1")
    assert cmsgs and cmsgs[0]["role"] == "system"


def test_evaluate_contract_compliance_variants():
    ev = evaluate_contract_compliance("hello", "world", None)
    assert ev["status"] == "PASS"
    text = "This always works and will prevent all scams."
    ev2 = evaluate_contract_compliance("start with the rubric table", text, "factory_truth_v1")
    assert ev2["status"] == "FAIL"


def test_parse_list_and_unknown_contract():
    out = parse_contract_ids(["factory_truth_v1", {"contract_id": "factory_truth_v1"}])
    assert out == ["factory_truth_v1"]
    with pytest.raises(KeyError):
        build_contract_system_messages("unknown_contract")


@pytest.mark.asyncio
async def test_chat_falls_back_to_max_output_tokens(monkeypatch):
    msg = SimpleNamespace(content="hello")
    response = SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    async def create(**kwargs):
        if "max_tokens" in kwargs:
            raise TypeError("unsupported")
        return response

    mock_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(council, "_client", lambda: mock_client)
    out = await council._chat("openai/gpt-5.2", [{"role": "user", "content": "x"}])
    assert out == "hello"


def test_get_openai_client_missing_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        council._get_openai_client()


def test_message_builders_include_expected_prompts():
    m = council._member_messages("openai/gpt-5.2", "u", None, stage="stage2")
    assert any("STAGE 2 EVALUATION MODE" in x["content"] for x in m if x["role"] == "system")
    ch = council._chairman_messages("openai/gpt-5.2", "p", "factory_truth_v1")
    assert any(x["role"] == "system" for x in ch)


def test_deep_extract_text_and_strip_wrappers():
    obj = {"foo": {"content": [{"text": "abc"}]}, "id": "chatcmpl-123456789012"}
    assert council._deep_extract_text(obj) == "abc"
    assert council._strip_wrappers("```txt\nFINAL_RANKING: A > B\n```") == "FINAL_RANKING: A > B"


@pytest.mark.asyncio
async def test_stage1_collect_responses_with_retry(monkeypatch):
    calls = {"google/gemini-3-pro-preview": 0}

    async def fake_chat(model, messages, temperature=0.2):
        if model == "google/gemini-3-pro-preview":
            calls[model] += 1
            if calls[model] == 1:
                return ""
        return "answer"

    monkeypatch.setattr(council, "_chat", fake_chat)
    res, errs = await council.stage1_collect_responses(
        "prompt",
        ["openai/gpt-5.2", "google/gemini-3-pro-preview"],
        contract_stack=None,
    )
    assert len(res) == 2
    assert res[1]["response"] == "answer"
    assert errs == {}


@pytest.mark.asyncio
async def test_stage2_collect_rankings_and_aggregate(monkeypatch):
    good = "\n".join(
        [
            "Response A: Strength: a; Flaw: fa",
            "Response B: Strength: b; Flaw: fb",
            "Response C: Strength: c; Flaw: fc",
            "Response D: Strength: d; Flaw: fd",
            "FINAL_RANKING: Response A > Response B > Response C > Response D",
        ]
    )

    async def fake_chat(model, messages, temperature=0.2):
        return good

    monkeypatch.setattr(council, "_chat", fake_chat)
    stage1 = [
        {"model": "m1", "response": "r1", "contract_eval": {"eligible": True}},
        {"model": "m2", "response": "r2", "contract_eval": {"eligible": True}},
        {"model": "m3", "response": "r3", "contract_eval": {"eligible": True}},
        {"model": "m4", "response": "r4", "contract_eval": {"eligible": True}},
    ]
    s2, l2m, errs = await council.stage2_collect_rankings("p", stage1, ["j1", "j2", "j3"], depth="quick")
    assert len(s2) == 3
    assert not errs
    agg = council.calculate_aggregate_rankings(s2, l2m, {"m1": {"eligible": True}})
    assert agg


@pytest.mark.asyncio
async def test_stage3_and_run_deliberation_error_paths(monkeypatch):
    async def fake_chat(model, messages, temperature=0.2):
        return "final answer"

    monkeypatch.setattr(council, "_chat", fake_chat)
    out = await council.stage3_synthesize_final(
        "p",
        [{"model": "m1", "response": "r"}],
        [{"model": "j", "ranking": "x", "parsed_ranking": ["Response A"]}],
        {"Response A": "m1"},
        [{"model": "m1", "average_rank": 1.0}],
        chairman_model="c",
    )
    assert out["response"] == "final answer"

    err = await council.run_deliberation("p", stage1_models=[], stage2_models=[], chairman_model="", timeout=30)
    assert err["error_type"] in {"RuntimeError", "Exception"}


def test_get_openai_client_uses_openrouter_base(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(council, "AsyncOpenAI", FakeClient)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)

    _ = council._get_openai_client()
    assert captured["api_key"] == "k"
    assert captured["base_url"] == "https://openrouter.ai/api/v1"


@pytest.mark.asyncio
async def test_stage3_exception_path(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(council, "_chat", boom)
    out = await council.stage3_synthesize_final(
        "p",
        [{"model": "m1", "response": "r"}],
        [{"model": "j", "ranking": "x", "parsed_ranking": ["Response A"]}],
        {"Response A": "m1"},
        [{"model": "m1", "average_rank": 1.0}],
        chairman_model="c",
    )
    assert out["contract_eval"]["status"] == "FAIL"


@pytest.mark.asyncio
async def test_run_deliberation_validation_errors():
    e1 = await council.run_deliberation("p", stage1_models=["m1"], stage2_models=[], chairman_model="c")
    assert e1["error_type"] == "RuntimeError"
    e2 = await council.run_deliberation("p", stage1_models=["m1"], stage2_models=["j1"], chairman_model="")
    assert e2["error_type"] == "RuntimeError"
