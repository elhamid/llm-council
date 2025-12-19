#!/opt/homebrew/bin/bash
set -euo pipefail

BASE="http://127.0.0.1:8001"
PY="./.venv/bin/python3"

PASS=0
TOTAL=2

for i in $(seq 1 "$TOTAL"); do
  OUT="/tmp/test_stage2_run_${i}.json"

  echo "===== RUN $i / $TOTAL ====="

  curl -sS -X POST "$BASE/api/conversations" \
    -H "Content-Type: application/json" \
    -d '{"title":"Stage2 smoke (2-run)","tags":["stage2","smoke"]}' > /tmp/convo_stage2.json

  CID=$("$PY" - <<'PY'
import json
print(json.load(open("/tmp/convo_stage2.json"))["id"])
PY
)
  echo "CID=$CID"

  curl -sS -X POST "$BASE/api/conversations/$CID/messages" \
    -H "Content-Type: application/json" \
    -d "{\"content\":\"Reply with exactly: OK\"}" > "$OUT"

  OK=$("$PY" - "$OUT" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
s2 = d.get("stage2") or []
non_partial = [x for x in s2 if not x.get("partial") and not x.get("synthetic")]
print("stage2.total_judges =", len(s2))
print("stage2.non_partial  =", len(non_partial), "(need >= 1)")
ok = (len(s2) >= 1) and (len(non_partial) >= 1)
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
echo "smoke_pass_rate = $PASS / $TOTAL"
echo "score = $((PASS*100/TOTAL))"
