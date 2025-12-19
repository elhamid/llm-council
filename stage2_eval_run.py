import json, os, re, time, urllib.request
from collections import Counter, defaultdict

BASE="http://127.0.0.1:8001"

def http_json(method, url, payload=None, timeout=180):
    data=None
    headers={"Content-Type":"application/json"}
    if payload is not None:
        data=json.dumps(payload).encode("utf-8")
    req=urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def make_convo(title):
    return http_json("POST", f"{BASE}/api/conversations", {"title":title,"tags":["stage2","eval"]})

def post_msg(cid, content):
    return http_json("POST", f"{BASE}/api/conversations/{cid}/messages", {"content":content})

def has_5line(txt):
    if not txt: return False
    lines=[ln.strip() for ln in txt.splitlines() if ln.strip()]
    if len(lines)!=5: return False
    for i,c in enumerate("ABCD"):
        if not lines[i].lower().startswith(f"response {c.lower()}:"):
            return False
    return lines[-1].strip().upper().startswith("FINAL_RANKING:")

def placeholder_ratio(txt):
    lines=[ln.strip() for ln in (txt or "").splitlines() if ln.strip()]
    if len(lines)<4: return 1.0
    ph=sum(1 for ln in lines[:4] if "insufficient signal in text" in ln.lower())
    return ph/4.0

def evidence_ok_line(line: str) -> bool:
    # Verifiable "concrete detail" proxies:
    # - contains a quote '...' or "..." or backticks `...`
    # - OR contains code-ish tokens or endpoints
    # - OR contains a digit
    s=line or ""
    if re.search(r'["\'][^"\']{3,}["\']', s): return True
    if "`" in s: return True
    if "$((" in s or "/api/" in s or "curl" in s: return True
    if re.search(r"\d", s): return True
    return False

def evidence_ok_judge(ranking_text: str):
    if not has_5line(ranking_text): return (False, 0)
    lines=[ln.strip() for ln in ranking_text.splitlines() if ln.strip()]
    crit=lines[:4]
    ok=sum(1 for ln in crit if evidence_ok_line(ln))
    return (ok>=3), ok

def top1(parsed):
    return parsed[0] if isinstance(parsed,list) and parsed else None

def load_prompts(path):
    out=[]
    with open(path,"r",encoding="utf-8") as f:
        for ln in f:
            ln=ln.strip()
            if ln: out.append(json.loads(ln))
    return out

def main():
    prompts=load_prompts("eval_stage2_prompts.jsonl")
    ts=time.strftime("%Y%m%d_%H%M%S")
    outdir=f"/tmp/stage2_eval_{ts}"
    os.makedirs(outdir, exist_ok=True)

    tot=defaultdict(int)
    top1_votes=defaultdict(Counter)

    for item in prompts:
        cid = make_convo(f"stage2-eval-{item['id']}")["id"]
        data = post_msg(cid, item["prompt"])
        path=os.path.join(outdir, f"{item['id']}.json")
        with open(path,"w",encoding="utf-8") as w: json.dump(data,w,ensure_ascii=False,indent=2)

        s2=data.get("stage2") or []
        np=[j for j in s2 if not j.get("partial")]
        tot["judges"] += len(s2)
        tot["non_partial"] += len(np)

        for j in s2:
            r=j.get("ranking") or ""
            if has_5line(r): tot["has5"] += 1
            if placeholder_ratio(r) <= 0.25: tot["no_placeholders"] += 1
            if (j.get("parsed_ranking") or []) != ["Response A","Response B","Response C","Response D"]:
                tot["divergent"] += 1
            if not j.get("partial"):
                ok, cnt = evidence_ok_judge(r)
                tot["evidence_checked"] += 1
                if ok: tot["evidence_ok"] += 1
                tot["evidence_lines_ok"] += cnt
                t1=top1(j.get("parsed_ranking") or [])
                if t1: top1_votes[item["id"]][t1]+=1

    print("\n== STAGE2 QUALITY EVAL (ROBUST EVIDENCE PROXY) ==")
    print("saved_json_dir:", outdir)
    print("total_judges           =", tot["judges"])
    print("non_partial_judges     =", tot["non_partial"])
    print("has5_rate              =", f"{tot['has5']}/{tot['judges']}")
    print("no_placeholder_rate    =", f"{tot['no_placeholders']}/{tot['judges']}")
    print("divergent_rate         =", f"{tot['divergent']}/{tot['judges']}")

    if tot["evidence_checked"]:
        avg_lines = tot["evidence_lines_ok"]/tot["evidence_checked"]
        print("evidence_ok_rate       =", f"{tot['evidence_ok']}/{tot['evidence_checked']}  (>=3/4 lines have concrete token)")
        print("avg_evidence_lines_ok  =", f"{avg_lines:.2f} / 4.00")

    print("\nTop-1 consensus per prompt (non-partial judges):")
    for pid, c in top1_votes.items():
        top, n = c.most_common(1)[0]
        total=sum(c.values())
        print(f"- {pid}: {top} ({n}/{total}) full={dict(c)}")

if __name__ == "__main__":
    main()
