from __future__ import annotations

from typing import Any

COST_PER_1K_TOKENS = {
    "openai/": 0.005,
    "anthropic/": 0.008,
    "google/": 0.003,
    "x-ai/": 0.005,
}


def _agreement_score(stage2_results: list[dict[str, Any]], labels: list[str]) -> float:
    valid_rankings = []
    for r in stage2_results:
        if r.get("partial") or r.get("synthetic"):
            continue
        parsed = r.get("parsed_ranking", [])
        if parsed:
            valid_rankings.append(parsed)

    if len(valid_rankings) < 2:
        return 0.0

    n_judges = len(valid_rankings)
    n_labels = len(labels)
    total_pairs = 0
    agreed_pairs = 0

    for i in range(n_judges):
        for j in range(i + 1, n_judges):
            for a_idx in range(n_labels):
                for b_idx in range(a_idx + 1, n_labels):
                    label_a = labels[a_idx]
                    label_b = labels[b_idx]
                    try:
                        pos_i_a = valid_rankings[i].index(label_a)
                        pos_i_b = valid_rankings[i].index(label_b)
                        pos_j_a = valid_rankings[j].index(label_a)
                        pos_j_b = valid_rankings[j].index(label_b)
                    except ValueError:
                        continue
                    total_pairs += 1
                    if (pos_i_a < pos_i_b) == (pos_j_a < pos_j_b):
                        agreed_pairs += 1

    return agreed_pairs / total_pairs if total_pairs > 0 else 0.0


def _estimate_cost(models_used: list[str], total_estimated_tokens: int = 15000) -> float:
    if not models_used:
        return 0.0
    per_model_tokens = total_estimated_tokens / len(models_used)
    total = 0.0
    for model in models_used:
        rate = 0.005
        for prefix, cost in COST_PER_1K_TOKENS.items():
            if model.startswith(prefix):
                rate = cost
                break
        total += (per_model_tokens / 1000) * rate
    return round(total, 4)


def calculate_metrics(
    duration_seconds: float,
    stage1_responses: list[dict[str, Any]],
    stage2_rankings: list[dict[str, Any]],
    aggregate_rankings: list[dict[str, Any]],
) -> dict[str, Any]:
    labels = [f"Response {chr(ord('A') + i)}" for i in range(len(stage1_responses))]
    models_used = sorted(
        {
            *(r.get("model") for r in stage1_responses if r.get("model")),
            *(r.get("model") for r in stage2_rankings if r.get("model")),
        }
    )
    agreement = _agreement_score(stage2_rankings, labels)

    dissent = 1.0 - agreement if stage2_rankings else 0.0
    return {
        "duration_seconds": round(float(duration_seconds), 3),
        "models_used": models_used,
        "agreement_score": round(agreement, 4),
        "dissent_ratio": round(dissent, 4),
        "cost_estimate_usd": _estimate_cost(models_used),
    }
