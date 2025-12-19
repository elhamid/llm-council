import re, json, glob, os
from collections import Counter, defaultdict

# choose newest /tmp/stage2_eval_* that contains at least 1 .json
dirs = sorted(glob.glob("/tmp/stage2_eval_*"))
DIR = None
for d in reversed(dirs):
    if os.path.isdir(d) and glob.glob(os.path.join(d, "*.json")):
        DIR = d
        break

if not DIR:
    print("No stage2 eval directories with JSON found under /tmp/stage2_eval_*")
    print("Run: python3 stage2_eval_run.py  (then rerun this)")
    raise SystemExit(1)

MD = "stage2_human_labels.md"
FILES = sorted(glob.glob(os.path.join(DIR, "*.json")))

print("DIR       =", DIR)
print("json_files=", len(FILES))
print("MD        =", MD, "(exists)" if os.path.exists(MD) else "(MISSING)")

if not os.path.exists(MD):
    raise SystemExit(f"Missing {MD}. Fill EXPECTED_TOP1 lines first.")

text = open(MD, "r", encoding="utf-8").read()

# Only treat top-level sections like: "## p1" .. "## pN"
sec_pat = re.compile(r"^##\s+(p\d+)\s*$", re.M)
starts = [(m.group(1), m.start()) for m in sec_pat.finditer(text)]

labels = {}
for i, (pid, start) in enumerate(starts):
    end = starts[i+1][1] if i+1 < len(starts) else len(text)
    block = text[start:end]
    m = re.search(r"^\*\*EXPECTED_TOP1:\*\*\s*(Response\s+[ABCD])\s*$", block, re.M)
    if m:
        labels[pid] = m.group(1)

print("labels_found =", len(labels), "->", dict(sorted(labels.items())))
if not labels:
    raise SystemExit("No EXPECTED_TOP1 labels found. Must be exactly: **EXPECTED_TOP1:** Response A")

acc = Counter()
tot = Counter()
top1_votes = defaultdict(Counter)
scored_prompts = 0

for fp in FILES:
    pid = os.path.basename(fp).replace(".json", "").strip()
    if pid not in labels:
        continue
    scored_prompts += 1
    gold = labels[pid]

    d = json.load(open(fp, "r", encoding="utf-8"))
    s2 = d.get("stage2") or []
    np = [j for j in s2 if not j.get("partial")]

    for j in np:
        model = j.get("model") or "unknown"
        pred = (j.get("parsed_ranking") or [None])[0]
        tot[model] += 1
        tot["__all__"] += 1
        if pred == gold:
            acc[model] += 1
            acc["__all__"] += 1
        if pred:
            top1_votes[pid][pred] += 1

print("\nscored_prompts =", scored_prompts)
if tot["__all__"] == 0:
    raise SystemExit("Scored 0 judge decisions. Likely pid mismatch between labels (p1..p6) and JSON filenames.")

print("\n== STAGE2 TRUE ACCURACY (vs your labels) ==")
for m in sorted([k for k in tot.keys() if k != "__all__"]):
    print(f"{m}: {acc[m]}/{tot[m]} = {acc[m]/tot[m]:.3f}")
print(f"\nOVERALL: {acc['__all__']}/{tot['__all__']} = {acc['__all__']/tot['__all__']:.3f}")

print("\nTop-1 consensus per prompt (non-partial judges):")
for pid, c in sorted(top1_votes.items()):
    top, n = c.most_common(1)[0]
    total = sum(c.values())
    print(f"- {pid}: {top} ({n}/{total}) full={dict(c)}")
