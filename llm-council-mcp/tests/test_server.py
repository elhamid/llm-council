from __future__ import annotations

import importlib
import json
import runpy

import pytest

import llm_council_mcp.models as models
import llm_council_mcp.server as server


@pytest.mark.asyncio
async def test_server_instantiation():
    assert server.mcp is not None


@pytest.mark.asyncio
async def test_tool_listing_has_three_tools():
    tool_manager = getattr(server.mcp, "_tool_manager", None)
    assert tool_manager is not None
    tools = list(getattr(tool_manager, "_tools", {}).keys())
    assert {"council_deliberate", "council_status", "council_configure", "council_feedback"}.issubset(set(tools))


@pytest.mark.asyncio
async def test_council_deliberate_parameter_validation():
    out = await server.council_deliberate(prompt="", depth="standard")
    data = json.loads(out)
    assert data["error_type"] == "ValidationError"


@pytest.mark.asyncio
async def test_council_deliberate_success(monkeypatch):
    async def fake_run(**kwargs):
        return {
            "answer": "final",
            "stage1_responses": [{"model": "m1", "response": "r1"}],
            "stage2_rankings": [{"model": "j1", "parsed_ranking": ["Response A"], "ranking": "raw"}],
            "aggregate_rankings": [{"model": "m1", "average_rank": 1.0, "rankings_count": 1}],
            "label_to_model": {"Response A": "m1"},
        }

    monkeypatch.setattr(server, "run_deliberation", fake_run)

    class FakeStore:
        def store_deliberation(self, **kwargs):
            return "id-1"

    monkeypatch.setattr(server, "_learning", FakeStore())
    out = await server.council_deliberate(prompt="hello")
    data = json.loads(out)
    assert data["answer"] == "final"
    assert data["metadata"]["learning_stored"] is True


@pytest.mark.asyncio
async def test_council_status_shape():
    out = await server.council_status()
    data = json.loads(out)
    assert "status" in data
    assert "config" in data
    assert "learning" in data


@pytest.mark.asyncio
async def test_council_configure_updates():
    out = await server.council_configure(stage1_models=["openai/gpt-5.2"], timeout_seconds=111)
    data = json.loads(out)
    assert data["status"] == "updated"
    assert data["config"]["timeout_seconds"] == 111


@pytest.mark.asyncio
async def test_error_handling_structured_json(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "run_deliberation", boom)
    out = await server.council_deliberate(prompt="hello")
    data = json.loads(out)
    assert data["error_type"] == "RuntimeError"


class _Ctx:
    def __init__(self):
        self.calls = []

    async def report_progress(self, current, total):
        self.calls.append(("progress", current, total))

    async def info(self, msg):
        self.calls.append(("info", msg))

    async def warning(self, msg):
        self.calls.append(("warning", msg))


@pytest.mark.asyncio
async def test_council_deliberate_invalid_depth():
    out = await server.council_deliberate(prompt="x", depth="bad")
    data = json.loads(out)
    assert data["error_type"] == "ValidationError"


@pytest.mark.asyncio
async def test_council_deliberate_ctx_and_run_error_payload(monkeypatch):
    async def fake_run(**kwargs):
        return {"error": "bad", "error_type": "Oops"}

    monkeypatch.setattr(server, "run_deliberation", fake_run)
    ctx = _Ctx()
    out = await server.council_deliberate(prompt="hello", ctx=ctx)
    data = json.loads(out)
    assert data["error"] == "bad"
    assert any(c[0] == "info" for c in ctx.calls)


@pytest.mark.asyncio
async def test_status_and_config_error_paths(monkeypatch):
    class BadLearning:
        db_path = "/tmp/x"

        def get_total_deliberations(self):
            raise RuntimeError("fail")

        def get_task_types_seen(self):
            return []

    monkeypatch.setattr(server, "_learning", BadLearning())
    out = await server.council_status()
    data = json.loads(out)
    assert data["error_type"] == "RuntimeError"

    def bad_update(**kwargs):
        raise RuntimeError("fail")

    monkeypatch.setattr(server, "update_config", bad_update)
    out2 = await server.council_configure()
    data2 = json.loads(out2)
    assert data2["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_council_deliberate_success_with_ctx(monkeypatch):
    async def fake_run(**kwargs):
        return {
            "answer": "final",
            "stage1_responses": [{"model": "m1", "response": "r1"}],
            "stage2_rankings": [{"model": "j1", "parsed_ranking": ["Response A"], "ranking": "raw"}],
            "aggregate_rankings": [{"model": "m1", "average_rank": 1.0, "rankings_count": 1}],
            "label_to_model": {"Response A": "m1"},
        }

    class FakeStore:
        def store_deliberation(self, **kwargs):
            return "id-2"

    monkeypatch.setattr(server, "run_deliberation", fake_run)
    monkeypatch.setattr(server, "_learning", FakeStore())
    ctx = _Ctx()
    out = await server.council_deliberate(prompt="hello", ctx=ctx)
    data = json.loads(out)
    assert data["metadata"]["learning_stored"] is True
    assert any(c[0] == "info" for c in ctx.calls)


@pytest.mark.asyncio
async def test_council_feedback_tool(monkeypatch):
    class FakeStore:
        def store_feedback(self, deliberation_id, quality_rating):
            return deliberation_id == "good-id" and quality_rating == 5

    monkeypatch.setattr(server, "_learning", FakeStore())
    bad = json.loads(await server.council_feedback(deliberation_id="", quality_rating=4))
    assert bad["error_type"] == "ValidationError"
    bad2 = json.loads(await server.council_feedback(deliberation_id="x", quality_rating=8))
    assert bad2["error_type"] == "ValidationError"
    nf = json.loads(await server.council_feedback(deliberation_id="missing", quality_rating=5))
    assert nf["error_type"] == "NotFoundError"
    ok = json.loads(await server.council_feedback(deliberation_id="good-id", quality_rating=5))
    assert ok["status"] == "updated"


def test_server_main_calls_stdio(monkeypatch):
    called = {"transport": None}

    def fake_run(*, transport):
        called["transport"] = transport

    monkeypatch.setattr(server.mcp, "run", fake_run)
    server.main()
    assert called["transport"] == "stdio"


def test_models_config_and_api(monkeypatch):
    monkeypatch.setenv("COUNCIL_MODELS", "a,b")
    monkeypatch.setenv("CHAIRMAN_MODEL", "c")
    monkeypatch.setenv("COUNCIL_TIMEOUT", "oops")
    monkeypatch.setenv("COUNCIL_MAX_TOKENS", "oops")
    importlib.reload(models)

    cfg = models.CouncilConfig()
    assert cfg.stage1_models == ["a", "b"]
    assert cfg.chairman_model == "c"
    models.update_config(stage1_models=["x"], stage2_models=["y"], chairman_model="z", timeout_seconds=12)
    assert models.get_config().timeout_seconds == 12

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert models.api_configured() is False
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert models.api_configured() is True


def test_main_module_runs(monkeypatch):
    called = {"ok": False}

    def fake_main():
        called["ok"] = True

    monkeypatch.setattr("llm_council_mcp.main", fake_main)
    runpy.run_module("llm_council_mcp.__main__", run_name="__main__")
    assert called["ok"] is True
