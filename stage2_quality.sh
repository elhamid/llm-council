#!/opt/homebrew/bin/bash
set -euo pipefail

BASE="http://127.0.0.1:8001"
PY="./.venv/bin/python3"

PASS=0
TOTAL=2

PROMPT=$("$PY" - <<'PY'
prompt = """You are given a broken bash snippet that should compute a pass rate.

Buggy snippet:
PASS=0; TOTAL=10
score = (PASS*100/TOTAL)

Fix it. Output a corrected snippet and a 1-sentence explanation."""
print(prompt)
PY
)

for i in $(seq 1 "$TOTAL"); do
  OUT="/tmp/test_stage2_quality_run_${i}.json"

  echo "===== RUN $i / $TOTAL ====="

  curl -sS -X POST "$BASE/api/conversations" \
    -H "Content-Type: application/json" \
    -d '{"title":"Stage2 quality (buggy snippet)","tags":["stage2","quality"]}' > /tmp/convo_stage2.json

  CID=$("$PY" - <<'PY'
import json
print(json.load(open("/tmp/convo_stage2.json"))["id"])
PY
)
  echo "CID=$CID"

  curl -sS -X POST "$BASE/api/conversations/$CID/messages" \
    -H "Content-Type: application/json" \
    -d "$(python3 - <<PY
import json
print(json.dumps({"content": """$PROMPT"""}))
PY
)" > "$OUT"

  OK=$("$PY" - "$OUT" <<'PY'
import json, sys

p = sys.argv[1]
d = json.load(open(p))
s2 = d.get("stage2") or []

def is_placeholder(line: str) -> bool:
    return "insufficient signal in text" in (line or "").strip().lower()

def has_5line(txt: str) -> bool:
    if not txt: return False
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    if len(lines) != 5: return False
    for i,c in enumerate(["A","B","C","D"]):
        if not lines[i].lower().startswith(f"response {c.lower()}:"):
            return False
    return lines[-1].strip().upper().startswith("FINAL_RANKING:")

def placeholder_ratio(txt: str) -> float:
    lines = [ln.strip() for ln in (txt or "").splitlines() if ln.strip()]
    if len(lines) < 4: return 1.0
    placeholders = sum(1 for ln in lines[:4] if is_placeholder(ln))
    return placeholders / 4.0

good = []
for x in s2:
    if x.get("synthetic") or x.get("partial"):
        continue
    r = x.get("ranking") or ""
    if has_5line(r) and placeholder_ratio(r) <= 0.25:
        good.append(x)

default = ["Response A","Response B","Response C","Response D"]
from collections import Counter

non_partial = [x for x in s2 if not x.get("partial")]
print("stage2.non_partial        =", len(non_partial))

reason_counts = Counter((x.get("partial_reason") or "") for x in s2 if x.get("partial"))
if reason_counts:
    items = sorted(reason_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    print("stage2.partial_reasons   =", ", ".join([f"{k}:{v}" for k,v in items]))
else:
    print("stage2.partial_reasons   =", "(none)")
# Consensus signals (non-partial judges only)
np = [x for x in s2 if not x.get("partial")]
tops = [((x.get("parsed_ranking") or [None])[0]) for x in np]
tops = [x for x in tops if x]
if tops:
    from collections import Counter
    c = Counter(tops)
    top_label, top_count = c.most_common(1)[0]
    print("stage2.top1_consensus    =", f"{top_label} ({top_count}/{len(np)})")
else:
    print("stage2.top1_consensus    =", "(none)")


# Per-judge health lines (avoid singling out any provider)
for idx, x in enumerate(sorted(s2, key=lambda y: (y.get("model") or ""))):
    m = x.get("model") or ""
    r = x.get("ranking") or ""
    p = bool(x.get("partial"))
    pr = (x.get("partial_reason") or "")
    has5 = has_5line(r)
    ph = placeholder_ratio(r) if has5 else 1.0
    parsed = x.get("parsed_ranking") or []
    div = bool(parsed) and (parsed != default)
    coerced = bool(x.get("coerced"))
    fix_used = bool(x.get("format_fix_used"))
    head = (r.replace("\n", " | ")[:140] if r else "")
    print(f"judge[{idx}] model={m} partial={p} reason={pr or '-'} has5={has5} placeholders={ph:.2f} divergent={div} coerced={coerced} fix_used={fix_used} head={head}")


ok = (len(good) >= 3)
print(1 if ok else 0)
PY
)

  echo "$OK"
  LAST=$(echo "$OK" | tail -n 1 | tr -d $'\r')
  if [ "$LAST" = "1" ]; then
    PASS=$((PASS+1))
  fi
  echo
done

echo "===== SUMMARY ====="
echo "stage2_launchable_pass_rate = $PASS / $TOTAL"
echo "score = $((PASS*100/TOTAL))"
