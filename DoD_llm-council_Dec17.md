# LLM Council Fork — Quality & Value Directive (Definition of Done)
Version: 1.0  
Scope: Public beta readiness (GitHub fork)  
Owner: Human (maintainer)  
Applies to: Stage 1 / Stage 2 / Stage 3 pipeline, evaluation scripts, and API output schema  
Non-goal: Rewriting architecture or removing existing functionality without explicit instruction.

---

## 0) Guardrails (Non-negotiable)
1. **Do not remove code unnecessarily.**  
   - Any deletion > ~20 lines must be justified in a short comment or commit note.
   - Prefer additive changes, feature flags, or compat shims.

2. **Schema stability is sacred.**  
   API response for `/api/conversations/{CID}/messages` must always include:
   - `stage1` (list)
   - `stage2` (list)
   - `stage3` (dict)
   - `meta` (dict)
   - `metadata` (dict)

3. **Failure must degrade gracefully.**  
   - No 500s for expected issues (missing key, provider error, timeout).
   - If a stage fails, return empty stage arrays and record errors in `meta.errors`.

4. **Deterministic wiring.**  
   - Stage 1 outputs must map to labels `Response A..D` (or configurable N).
   - Stage 2 and Stage 3 must use the same label map.

---

## 1) System Objective (What “good” means)
The Council is valuable only if it produces a final answer that is:
- **More correct** than most individual responses,
- **More complete** than the winner alone,
- **More actionable** for a non-technical user,
- **More robust** under edge cases and adversarial inputs,
- **Traceable** (we can explain why the final answer is what it is).

---

## 2) Stage 1 — Intentional Diversity (DoD)
Stage 1 must produce deliberately different perspectives (not four near-duplicates).

### DoD Requirements
- Each Stage 1 responder runs under an explicit **role** (system prompt) that causes diversity:
  - **Builder**: fastest correct implementation
  - **Skeptic**: attacks assumptions / failure modes
  - **Minimalist**: smallest diff / simplest steps
  - **Auditor**: security, abuse-resistance, operational risk
- Stage 1 output schema per item must include at minimum:
  - `model` (string)
  - `response` (string)
  - `contract_eval` (object|null)

### Success Criteria
- On a prompt suite, Stage 1 answers should meaningfully differ in focus, not just wording.

---

## 3) Stage 2 — Structured Judging (DoD)
Stage 2 must produce not only a ranking, but **machine-usable signals** to guide synthesis.

### DoD Output Schema per judge
Each Stage 2 judge item must include:
- `model`
- `ranking` (the 5-line format below)
- `parsed_ranking` (array of labels)
- `raw_ranking` (original text if needed)
- `partial` (bool)
- `partial_reason` (string, optional)
- Optional: `adjudicator` (bool)

### Required 5-line ranking format
Exactly 5 lines:
1. `Response A: Strength: ...; Flaw: ...`
2. `Response B: Strength: ...; Flaw: ...`
3. `Response C: Strength: ...; Flaw: ...`
4. `Response D: Strength: ...; Flaw: ...`
5. `FINAL_RANKING: Response X > Response Y > Response Z > Response W`

### Evidence rule (parser-verifiable)
Each critique line (A–D) must include **at least one concrete token** present in that response (code token, identifier, keyword, specific phrase).
- Example compliant: “uses `$((...))`”, “mentions `TOTAL=0`”, “calls `/api/conversations/{CID}/messages`”
- Example noncompliant: “good clarity”, “seems correct”, “nice structure” (no overlap)

### Stage 2 scoring vector (required, even if embedded)
Stage 2 must internally compute or emit (in meta or per-judge) a score vector per response:
- correctness
- completeness
- actionability
- risk/safety
- clarity
- contract compliance (format + evidence)

If you cannot change schema, store this under `meta.stage2_scores` (do not break existing fields).

### Partial handling
A judge must be marked `partial=true` if:
- any placeholder critiques appear (“Insufficient signal…”)
- ranking format invalid
- parsed_ranking cannot be derived reliably
Partial judges are excluded from consensus and synthesis weighting.

---

## 4) Adjudication (Option B3) — Minimal & High-ROI (DoD)
Adjudication exists only to resolve ambiguity when it matters.

### Trigger Conditions (any triggers adjudication)
- **Consensus is weak** (top-1 split close): e.g. top1_count / non_partial_judges < 0.60
- **Evidence proxy fails**: evidence_ok_rate < 0.75
- **Partial rate rises**: partial_judges / total_judges > 0.10
- **Divergence is extreme**: no two judges share top1

### Adjudicator job
Adjudicator must:
- Re-read Stage 1 responses and Stage 2 rationales
- Choose a base response (winner)
- Identify improvements from other responses **by rubric dimension**
- Output a structured adjudication summary (can be stored in `meta.adjudication`)

### Cost control
Adjudication is off by default and only runs on trigger.

---

## 5) Stage 3 — “Best-of + Merge” Synthesis (DoD)
Stage 3 is not a rewriter. It is an editor-in-chief that merges improvements.

### DoD Behavior
- Stage 3 selects a **base** response (usually Stage 2 winner, unless adjudicator says otherwise).
- Stage 3 **incorporates valid improvements** from other responses when they improve a specific rubric dimension.
- Stage 3 must reject low-quality/incorrect suggestions explicitly (internally or in meta).

### Stage 3 Output Requirements
Stage 3 output must include:
- `model`
- `response` (final answer)
- `contract_eval` (object|null)

Additionally, for traceability (prefer meta keys if schema must remain stable):
- `meta.stage3_base_label`: `Response A|B|C|D`
- `meta.stage3_contributors`: list of `{label, reason, dimension}`
- `meta.stage3_rejections`: list of `{label, rejected_point, reason}` (optional but recommended)

### “Single winner” still requires synthesis
Even if one answer is top1, Stage 3 must check other answers for:
- missing edge cases
- clearer steps
- security issues
- contradictions
…and merge improvements when valid.

---

## 6) Reliability & Error Handling (DoD)
### No hard 500s for expected issues
Any exception in stage calls must:
- be caught
- recorded in `meta.errors`
- return a valid response schema with empty stages as needed

### Provider/API key checks
- If missing API key: return 500 with a clear message **before** attempting any model call.
- If provider errors mid-run: mark the affected stage item as partial and continue where possible.

### Timeouts and retries
- Set sane timeouts per model call.
- Retry only for transient errors (429, 502/503) with capped backoff.
- Avoid infinite loops.

---

## 7) Security & Abuse-Resistance (Public Beta) (DoD)
### Minimum viable protections
- Input size limit (max bytes) with 413 response.
- Optional bearer auth token (off by default).
- CORS is configurable; defaults safe for local dev.

### Abuse-resistance
- Rate limit knobs (even if only documented config): per-IP/per-minute or global.
- Log redaction for keys and secrets.
- Prevent prompt injection from overwriting system prompts (system prompts must be server-side constants).

---

## 8) Docs & Packaging for GitHub (DoD)
### Repository must include
- README with:
  - what this fork does (stages + roles + adjudication)
  - setup steps
  - env vars
  - how to run server
  - how to run eval
  - known limitations
- Minimal “Troubleshooting” section:
  - missing API key
  - OpenRouter base URL
  - common 500 causes + how to enable debug logging
- Versioning:
  - `/health` returns `{status, version}`
  - changelog notes for breaking changes

---

## 9) Evaluation — Passing Threshold (95% Quality & Value)
To pass public beta threshold, the fork must meet the following on the standard prompt pack:

### Reliability gates (must pass)
- Smoke loop: ≥ 0.95 pass rate (no schema regressions, no 500s)
- Stage 2 non-partial judges: ≥ 0.90
- No placeholder critiques: ≥ 0.95

### Quality/value gates (must pass)
- Evidence rule compliance (proxy): ≥ 0.85
- Stage 3 “merge behavior” verified on at least 3 targeted prompts:
  - Final response includes at least 1 valid improvement from a non-winner when present.
- Adjudication triggers only when needed; when triggered:
  - top1 consensus improves or rationale becomes clearly grounded.

### Reporting
The eval script must print a single summary block including:
- total_judges / non_partial_judges
- has5_rate
- no_placeholder_rate
- evidence_ok_rate
- top1_consensus per prompt
- adjudicator occurrences

---

## 10) Implementation Policy (How to change code)
- Prefer compatibility shims over refactors.
- If a function signature changed, adapt callers defensively (signature inspection or wrapper).
- Keep backwards-compatible aliases for renamed functions when practical.
- Every change must be validated by:
  1) one cheap smoke command
  2) full eval command
- Never “fix” by deleting entire subsystems (roles, contracts, storage, adjudication).

---

## 11) Definition of Done Checklist (Quick)
- [ ] API schema stable; no 500s on normal use
- [ ] Stage 1 role-driven diversity
- [ ] Stage 2: strict 5-line format + evidence token rule
- [ ] Partial handling correct; no placeholders
- [ ] Stage 3: best-of + merge + provenance in meta
- [ ] Adjudication (B3) triggers and logs correctly
- [ ] Docs + setup + eval instructions complete
- [ ] Eval achieves ≥ 95% on reliability + ≥ 95% on quality/value gates
