"""Retrieval scoring (Hit@1, Evidence Hit@3) with full per-query traces.

A retrieved chunk is a hit when it comes from a gold (document, page) AND
contains the whole evidence quote after normalization. Coherence and
grounding are NOT inferred here; they are labelled manually.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def _on_gold_page(question: dict, hit: dict) -> bool:
    return any(hit["document_id"] == g["source_id"] and int(hit["page"]) == int(g["page"])
               for g in question["gold"])


def is_hit(question: dict, hit: dict) -> bool:
    """Hit = chunk on a gold page that contains that gold's full evidence
    (a gold entry may carry its own evidence_quote; default is the question's)."""
    text = norm(hit["text"])
    for g in question["gold"]:
        if hit["document_id"] == g["source_id"] and int(hit["page"]) == int(g["page"]):
            if norm(g.get("evidence_quote", question["evidence_quote"])) in text:
                return True
    return False


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]], strategy: str,
                   applied_filter: dict | None) -> dict[str, Any]:
    flags = [is_hit(question, h) for h in hits]
    on_page = [_on_gold_page(question, h) for h in hits]
    return {
        "strategy": strategy,
        "id": question["id"],
        "split": question["split"],
        "type": question.get("type"),
        "question": question["question"],
        "expected_answer": question["expected_answer"],
        "applied_filter": applied_filter,
        "gold": question["gold"],
        "evidence_quote": question["evidence_quote"],
        "hit@1": bool(flags and flags[0]),
        "hit@3": any(flags[:3]),
        "gold_page_in_top3": any(on_page[:3]),
        # gold page retrieved but no single chunk holds the full evidence
        "evidence_split_or_partial": any(on_page[:3]) and not any(flags[:3]),
        "top3": [{"rank": i + 1, "chunk_id": h["chunk_id"], "document_id": h["document_id"],
                  "page": h["page"], "score": round(float(h["score"]), 4),
                  "metadata": h["metadata"], "on_gold_page": p, "is_hit": f,
                  **({"ranks": h["ranks"]} if "ranks" in h else {}),
                  "text": h["text"]}
                 for i, (h, f, p) in enumerate(zip(hits, flags, on_page))],
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    agg: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"n": 0, "hit1": 0, "hit3": 0, "page3": 0, "split": 0})
    for t in traces:
        for split in (t["split"], "all"):
            a = agg[(t["strategy"], split)]
            a["n"] += 1
            a["hit1"] += t["hit@1"]
            a["hit3"] += t["hit@3"]
            a["page3"] += t["gold_page_in_top3"]
            a["split"] += t["evidence_split_or_partial"]
    out = {}
    for (strategy, split), a in sorted(agg.items()):
        n = a["n"]
        out[f"{strategy}/{split}"] = {
            "N": n,
            "Hit@1": f"{a['hit1']}/{n}", "Hit@1_rate": round(a["hit1"] / n, 3),
            "EvidenceHit@3": f"{a['hit3']}/{n}", "EvidenceHit@3_rate": round(a["hit3"] / n, 3),
            "GoldPage@3": f"{a['page3']}/{n}",
            "evidence_split_or_partial": a["split"],
        }
    return out
