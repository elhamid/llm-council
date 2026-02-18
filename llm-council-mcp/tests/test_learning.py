from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from llm_council_mcp.learning import LearningStore, detect_task_type


def test_db_creation(tmp_db):
    store = LearningStore(db_path=tmp_db)
    assert store.get_total_deliberations() == 0


def test_store_deliberation_and_feedback(tmp_db):
    store = LearningStore(db_path=tmp_db)
    did = store.store_deliberation(
        prompt="compare microservices vs monolith architecture",
        task_type="architectural_decision",
        models=["openai/gpt-5.2"],
        chairman="anthropic/claude-opus-4.5",
        duration_seconds=5.0,
        agreement_score=0.8,
        dissent_ratio=0.2,
        cost_estimate_usd=0.04,
        aggregate_rankings=[{"model": "openai/gpt-5.2", "average_rank": 1.0}],
        depth="standard",
    )
    assert did
    assert store.get_total_deliberations() == 1
    assert store.store_feedback(did, 4)


def test_get_model_stats_and_best_models(tmp_db):
    store = LearningStore(db_path=tmp_db)
    store.store_deliberation(
        prompt="review code quality",
        task_type="code_review",
        models=["openai/gpt-5.2"],
        chairman="anthropic/claude-opus-4.5",
        duration_seconds=5.0,
        agreement_score=0.8,
        dissent_ratio=0.2,
        cost_estimate_usd=0.04,
        aggregate_rankings=[{"model": "openai/gpt-5.2", "average_rank": 1.0}],
    )
    assert store.get_model_stats(limit=5)
    assert store.get_best_models_for_task_type("code_review", limit=5)


def test_detect_task_type():
    assert detect_task_type("please review this code and refactor") == "code_review"
    assert detect_task_type("microservices architecture tradeoff") in {"architectural_decision", "tradeoff_analysis"}


def test_concurrent_access(tmp_db):
    store = LearningStore(db_path=tmp_db)

    def write_one(i: int):
        store.store_deliberation(
            prompt=f"debug error {i}",
            task_type="debugging",
            models=["openai/gpt-5.2"],
            chairman="anthropic/claude-opus-4.5",
            duration_seconds=1.0,
            agreement_score=0.5,
            dissent_ratio=0.5,
            cost_estimate_usd=0.01,
            aggregate_rankings=[{"model": "openai/gpt-5.2", "average_rank": 1.0}],
        )

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(write_one, range(8)))

    assert store.get_total_deliberations() == 8
