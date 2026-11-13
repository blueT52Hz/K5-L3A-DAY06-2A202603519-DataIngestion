"""Check benchmark/questions.jsonl BEFORE any retrieval run.

- every evidence_quote occurs (after normalization) on at least one gold page
- >= 20 questions, >= 15 dev / >= 5 test, >= 3 filter questions with >= 1 in test
- filter values exist in the corpus metadata and never contain the gold source id
Run from starter/:  python scripts/verify_benchmark.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.evaluation import norm  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
pages = {}
for line in (ROOT / "results" / "pages.jsonl").read_text(encoding="utf-8").splitlines():
    p = json.loads(line)
    pages[(p["document_id"], p["page"])] = p

qs = [json.loads(l) for l in (ROOT / "benchmark" / "questions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
problems = []
ids = Counter(q["id"] for q in qs)
problems += [f"duplicate id {i}" for i, n in ids.items() if n > 1]
for q in qs:
    for g in q["gold"]:  # every listed gold page must really contain its evidence
        ev = norm(g.get("evidence_quote", q["evidence_quote"]))
        if ev not in norm(pages.get((g["source_id"], g["page"]), {}).get("text", "")):
            problems.append(f"{q['id']}: evidence not found on gold page {g['page']}")
    flt = q.get("filter")
    if flt:
        for k, v in flt.items():
            if any(g["source_id"] == str(v) for g in q["gold"]):
                problems.append(f"{q['id']}: filter leaks gold source")
            if not any(str(p["metadata"].get(k)) == str(v) for p in pages.values()):
                problems.append(f"{q['id']}: filter value {k}={v} not present in corpus")
        for g in q["gold"]:
            meta = pages.get((g["source_id"], g["page"]), {}).get("metadata", {})
            if any(str(meta.get(k)) != str(v) for k, v in flt.items()):
                problems.append(f"{q['id']}: gold page {g['page']} does not satisfy filter {flt}")

split = Counter(q["split"] for q in qs)
n_filter = sum(1 for q in qs if q.get("filter"))
n_filter_test = sum(1 for q in qs if q.get("filter") and q["split"] == "test")
print(f"questions={len(qs)} split={dict(split)} filter={n_filter} (test={n_filter_test}) "
      f"types={dict(Counter(q.get('type') for q in qs))}")
if len(qs) < 20 or split["dev"] < 15 or split["test"] < 5:
    problems.append("need >= 20 questions with >= 15 dev / >= 5 test")
if n_filter < 3 or n_filter_test < 1:
    problems.append("need >= 3 filter questions with >= 1 in test")
print("OK: all evidence verified" if not problems else "PROBLEMS:\n- " + "\n- ".join(problems))
sys.exit(1 if problems else 0)
