from llm_council_mcp.prompts import (
    build_chairman_prompt,
    build_stage2_one_line_repair_prompt,
    build_stage2_rewrite_prompt,
    build_stage2_rubric,
    build_stage2_strict_rejudge_prompt,
    example_ranking,
)


def test_build_stage2_rubric_includes_labels():
    labels = ["Response A", "Response B", "Response C", "Response D"]
    rank = example_ranking(labels)
    rubric = build_stage2_rubric(labels, rank)
    for label in labels:
        assert label in rubric


def test_example_ranking_anti_anchor():
    labels = ["Response A", "Response B", "Response C", "Response D"]
    assert example_ranking(labels) == "Response B > Response C > Response A > Response D"


def test_example_ranking_empty_labels():
    assert example_ranking([]) == "Response B > Response C > Response A > Response D"


def test_build_chairman_prompt_contains_stage_data(sample_stage1_results, sample_stage2_results):
    prompt = build_chairman_prompt("what should we do", sample_stage1_results, sample_stage2_results, [{"model": "m", "average_rank": 1.0}])
    assert "USER PROMPT" in prompt
    assert "STAGE 1 OUTPUTS" in prompt
    assert "STAGE 2 OUTPUTS" in prompt
    assert "AGGREGATE RANKINGS" in prompt


def test_repair_prompts_generated():
    labels = ["Response A", "Response B", "Response C"]
    example = example_ranking(labels)
    strict = build_stage2_strict_rejudge_prompt(labels, example, "BASE")
    rewrite = build_stage2_rewrite_prompt(labels, example, "raw")
    one_line = build_stage2_one_line_repair_prompt(labels)
    assert "EXACTLY 4 LINES" in strict
    assert "TEXT TO REWRITE" in rewrite
    assert "FINAL_RANKING" in one_line
