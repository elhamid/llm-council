from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path.home() / ".llm-council" / "deliberations.db"

TASK_TYPE_PATTERNS = {
    "architectural_decision": r"\b(architect|system design|infrastructure|scaling|microservices|monolith)\b",
    "code_review": r"\b(review|refactor|code quality|best practice|clean code|lint)\b",
    "tradeoff_analysis": r"\b(tradeoff|trade-off|pros and cons|compare|versus|vs\.?|alternative)\b",
    "debugging": r"\b(bug|error|fix|crash|exception|traceback|stack trace|debug)\b",
    "security_review": r"\b(security|auth|authentication|authorization|vulnerability|injection|xss|csrf)\b",
    "strategic_planning": r"\b(strategy|roadmap|planning|milestone|okr|kpi|goal)\b",
    "creative_synthesis": r"\b(brainstorm|creative|novel|innovative|design thinking)\b",
}


def detect_task_type(prompt: str) -> str:
    prompt_lower = prompt.lower()
    scores = {}
    for task_type, pattern in TASK_TYPE_PATTERNS.items():
        matches = re.findall(pattern, prompt_lower, re.IGNORECASE)
        if matches:
            scores[task_type] = len(matches)
    if not scores:
        return "general"
    return max(scores, key=scores.get)


class LearningStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS deliberations (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    prompt_hash TEXT NOT NULL,
                    task_type TEXT DEFAULT 'unknown',
                    models TEXT NOT NULL,
                    chairman TEXT NOT NULL,
                    duration_seconds REAL,
                    agreement_score REAL,
                    dissent_ratio REAL,
                    cost_estimate_usd REAL,
                    quality_rating INTEGER,
                    depth TEXT DEFAULT 'standard'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS model_rankings (
                    deliberation_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    average_rank REAL,
                    FOREIGN KEY (deliberation_id) REFERENCES deliberations(id)
                )
                """
            )

    def store_deliberation(
        self,
        prompt: str,
        task_type: str,
        models: list[str],
        chairman: str,
        duration_seconds: float,
        agreement_score: float,
        dissent_ratio: float,
        cost_estimate_usd: float,
        aggregate_rankings: list[dict[str, Any]],
        depth: str = "standard",
    ) -> str:
        deliberation_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO deliberations (
                    id, timestamp, prompt_hash, task_type, models, chairman,
                    duration_seconds, agreement_score, dissent_ratio, cost_estimate_usd, depth
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deliberation_id,
                    timestamp,
                    prompt_hash,
                    task_type or detect_task_type(prompt),
                    json.dumps(models),
                    chairman,
                    float(duration_seconds),
                    float(agreement_score),
                    float(dissent_ratio),
                    float(cost_estimate_usd),
                    depth,
                ),
            )

            for ranking in aggregate_rankings:
                conn.execute(
                    "INSERT INTO model_rankings (deliberation_id, model, average_rank) VALUES (?, ?, ?)",
                    (
                        deliberation_id,
                        ranking.get("model", ""),
                        float(ranking.get("average_rank", 9999.0)),
                    ),
                )

        return deliberation_id

    def store_feedback(self, deliberation_id: str, quality_rating: int) -> bool:
        if quality_rating < 1 or quality_rating > 5:
            return False
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE deliberations SET quality_rating = ? WHERE id = ?",
                (int(quality_rating), deliberation_id),
            )
            return cur.rowcount > 0

    def get_model_stats(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT model, AVG(average_rank) as avg_rank, COUNT(*) as samples
                FROM model_rankings
                GROUP BY model
                ORDER BY avg_rank ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [{"model": r[0], "average_rank": float(r[1]), "samples": int(r[2])} for r in rows]

    def get_best_models_for_task_type(self, task_type: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT mr.model, AVG(mr.average_rank) as avg_rank, COUNT(*) as samples
                FROM model_rankings mr
                JOIN deliberations d ON d.id = mr.deliberation_id
                WHERE d.task_type = ?
                GROUP BY mr.model
                ORDER BY avg_rank ASC
                LIMIT ?
                """,
                (task_type, int(limit)),
            ).fetchall()
        return [{"model": r[0], "average_rank": float(r[1]), "samples": int(r[2])} for r in rows]

    def get_total_deliberations(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM deliberations").fetchone()
        return int(row[0] if row else 0)

    def get_task_types_seen(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT task_type FROM deliberations ORDER BY task_type ASC").fetchall()
        return [r[0] for r in rows if r[0]]
