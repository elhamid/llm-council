from llm_council_mcp.metrics import _agreement_score, _estimate_cost, calculate_metrics


def test_agreement_score_unanimous():
    r = [
        {"parsed_ranking": ["Response A", "Response B", "Response C", "Response D"], "partial": False},
        {"parsed_ranking": ["Response A", "Response B", "Response C", "Response D"], "partial": False},
    ]
    assert _agreement_score(r, ["Response A", "Response B", "Response C", "Response D"]) == 1.0


def test_agreement_score_split():
    r = [
        {"parsed_ranking": ["Response A", "Response B", "Response C", "Response D"], "partial": False},
        {"parsed_ranking": ["Response D", "Response C", "Response B", "Response A"], "partial": False},
    ]
    assert _agreement_score(r, ["Response A", "Response B", "Response C", "Response D"]) < 0.2


def test_agreement_score_no_valid_judges():
    assert _agreement_score([], ["Response A", "Response B"]) == 0.0


def test_agreement_score_skips_partial_and_missing_labels():
    r = [
        {"parsed_ranking": ["Response A", "Response B"], "partial": True},
        {"parsed_ranking": ["Response A", "Response Z"], "partial": False},
        {"parsed_ranking": ["Response B", "Response A"], "partial": False},
    ]
    score = _agreement_score(r, ["Response A", "Response B", "Response C"])
    assert score == 0.0


def test_estimate_cost_with_mix():
    cost = _estimate_cost(["openai/gpt-5.2", "google/gemini-3-pro-preview"])
    assert cost > 0


def test_estimate_cost_empty():
    assert _estimate_cost([]) == 0.0


def test_calculate_metrics_shape():
    m = calculate_metrics(
        duration_seconds=10.2,
        stage1_responses=[{"model": "openai/gpt-5.2"}],
        stage2_rankings=[{"model": "openai/gpt-5.2", "parsed_ranking": ["Response A"], "partial": False}],
        aggregate_rankings=[],
    )
    for key in ["duration_seconds", "models_used", "agreement_score", "dissent_ratio", "cost_estimate_usd"]:
        assert key in m
