"""Day 06 pipeline entry point.

Run from the `starter/` directory:

    python -m src.run ingest                 # read PDF -> chunk A/B -> embed -> pgvector
    python -m src.run retrieve --split dev   # top-3 for A/B/C on dev questions
    python -m src.run retrieve --split test  # only after the strategy is frozen on dev
    python -m src.run audit-prep             # CSV sheets for manual labelling
    python -m src.run audit-summary          # merge manual labels -> results/manual_summary.json
    python -m src.run tune-hybrid            # optional: RRF grid on dev only
    python -m src.run retrieve --split test --strategies D   # hybrid vs baseline on test

Strategy C reuses the chunks/vectors of B and only adds the metadata filter,
so B vs C differs in exactly one factor.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .chunking import chunk_pages
from .evaluation import evaluate_query, norm, summarize
from .ingestion import load_corpus
from .models import DocumentPage

STARTER = Path(__file__).resolve().parents[1]
ROOT = STARTER.parent
CONFIG_PATH = STARTER / "configs" / "strategies.json"
MANIFEST = ROOT / "data" / "manifest.json"
QUESTIONS = ROOT / "benchmark" / "questions.jsonl"
RESULTS = ROOT / "results"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _store_and_embedder(cfg: dict):
    from .embeddings import LocalE5Embedder
    from .store import PgVectorStore
    model_name = os.environ.get("EMBEDDING_MODEL") or cfg["embedding_model"]
    if model_name != cfg["embedding_model"]:
        raise ValueError(f"EMBEDDING_MODEL={model_name} differs from config {cfg['embedding_model']}")
    embedder = LocalE5Embedder(model_name)
    store = PgVectorStore(os.environ["DATABASE_URL"], embedder.dimensions)
    store.create_schema()
    return store, embedder


# ------------------------------------------------------------------ ingest
def cmd_ingest(args) -> None:
    cfg = _load_config()
    t0 = time.perf_counter()
    pages, errors = load_corpus(MANIFEST, ROOT)
    read_s = time.perf_counter() - t0
    _write_jsonl(RESULTS / "pages.jsonl", [p.__dict__ for p in pages])
    _write_jsonl(RESULTS / "ingest_errors.jsonl", errors)
    page_index = [{"document_id": p.document_id, "page": p.page, "chars": len(p.text),
                   "chapter": p.metadata["chapter"], "section": p.metadata.get("section")}
                  for p in pages]
    (ROOT / "data" / "page_index.json").write_text(
        json.dumps({"content_pages": len(pages), "total_chars": sum(len(p.text) for p in pages),
                    "pages": page_index}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Read {len(pages)} content pages in {read_s:.2f}s; {len(errors)} logged page issues")
    if args.pages_only:
        return

    store, embedder = _store_and_embedder(cfg)
    stats = {"run_at": _now(), "embedding_model": embedder.model_name,
             "dimensions": embedder.dimensions, "device": "cpu",
             "python": platform.python_version(), "content_pages": len(pages),
             "read_seconds": round(read_s, 2), "strategies": {}}
    for name in cfg["ingest_strategies"]:  # A and B (C reuses B)
        params = cfg["strategies"][name]
        before = store.count(name)
        t1 = time.perf_counter()
        chunks = chunk_pages(pages, name, params)
        t2 = time.perf_counter()
        vectors = embedder.embed_documents([c.text for c in chunks])
        t3 = time.perf_counter()
        result = store.upsert(name, chunks, vectors)
        t4 = time.perf_counter()
        lens = [len(c.text) for c in chunks]
        stats["strategies"][name] = {
            "params": params, "chunk_count": len(chunks),
            "db_count_before": before, "db_count_after": store.count(name), **result,
            "len_min": min(lens), "len_median": statistics.median(lens),
            "len_mean": round(statistics.mean(lens), 1), "len_max": max(lens),
            "total_chars": sum(lens), "approx_tokens": round(sum(lens) / 4),
            "chunk_seconds": round(t2 - t1, 2), "embed_seconds": round(t3 - t2, 2),
            "store_seconds": round(t4 - t3, 2), "cost_usd": 0.0,
        }
        _write_jsonl(RESULTS / f"chunks_{name}.jsonl", [c.__dict__ for c in chunks])
        print(f"[{name}] {len(chunks)} chunks | db before={before} after={store.count(name)} "
              f"| embed {t3 - t2:.1f}s")
    out = RESULTS / "ingest_stats.json"
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    with (RESULTS / "reingest_log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"run_at": stats["run_at"], **{
            k: {"before": v["db_count_before"], "after": v["db_count_after"],
                "chunk_count": v["chunk_count"], "deleted_obsolete": v["deleted_obsolete"]}
            for k, v in stats["strategies"].items()}}) + "\n")


# ---------------------------------------------------------------- retrieve
def _run_strategy(store, q: dict, qv: list[float], name: str, params: dict,
                  hybrid_override: dict | None = None) -> dict:
    table = params.get("reuse_chunks_of", name)
    flt = q.get("filter") if params.get("use_filter") and q.get("filter") else None
    hybrid = hybrid_override or params.get("hybrid")
    t0 = time.perf_counter()
    if hybrid:
        hits = store.hybrid_search(table, q["question"], qv, top_k=3, filters=flt, **hybrid)
    else:
        hits = store.search(table, qv, top_k=3, filters=flt)
    trace = evaluate_query(q, hits, name, flt)
    trace["search_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    trace["searched_chunks_of"] = table
    if hybrid:
        trace["hybrid_params"] = hybrid
    return trace


def cmd_retrieve(args) -> None:
    cfg = _load_config()
    store, embedder = _store_and_embedder(cfg)
    names = args.strategies.split(",") if args.strategies else cfg["eval_strategies"]
    questions = [q for q in _read_jsonl(QUESTIONS) if args.split == "all" or q["split"] == args.split]
    traces = []
    for q in questions:
        qv = embedder.embed_query(q["question"])
        for name in names:
            traces.append(_run_strategy(store, q, qv, name, cfg["strategies"][name]))
    suffix = f"_{'_'.join(names)}" if args.strategies else ""
    _write_jsonl(RESULTS / f"traces_{args.split}{suffix}.jsonl", traces)
    summary = {"run_at": _now(), "split": args.split, "embedding_model": embedder.model_name,
               "strategies": names, "metrics": summarize(traces)}
    (RESULTS / f"summary_{args.split}{suffix}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["metrics"], ensure_ascii=False, indent=2))


# ------------------------------------------------------------ hybrid tuning
def cmd_tune_hybrid(args) -> None:
    """Grid search of RRF parameters on the DEV split only (never test).

    Selection rule (fixed before running): highest dev Evidence Hit@3, then Hit@1,
    then the configuration closest to the RRF default (k=60, w_keyword=1.0).
    """
    cfg = _load_config()
    store, embedder = _store_and_embedder(cfg)
    params = cfg["strategies"]["D"]
    dev = [q for q in _read_jsonl(QUESTIONS) if q["split"] == "dev"]
    vectors = {q["id"]: embedder.embed_query(q["question"]) for q in dev}
    grid = [{"k": k, "w_dense": 1.0, "w_keyword": w, "depth": 20}
            for k in (10, 30, 60) for w in (0.5, 1.0, 2.0)]
    rows = []
    for hp in grid:
        traces = [_run_strategy(store, q, vectors[q["id"]], "D", params, hp) for q in dev]
        hit1 = sum(t["hit@1"] for t in traces)
        hit3 = sum(t["hit@3"] for t in traces)
        rows.append({**hp, "dev_hit1": f"{hit1}/{len(dev)}", "dev_hit3": f"{hit3}/{len(dev)}",
                     "_h1": hit1, "_h3": hit3,
                     "per_question_hit3": {t["id"]: t["hit@3"] for t in traces}})
        print(f"k={hp['k']:>2} w_kw={hp['w_keyword']:<3} -> Hit@1 {hit1}/{len(dev)}  "
              f"Hit@3 {hit3}/{len(dev)}")
    best = max(rows, key=lambda r: (r["_h3"], r["_h1"], -abs(r["k"] - 60), -abs(r["w_keyword"] - 1.0)))
    out = {"run_at": _now(), "split": "dev", "rule": cmd_tune_hybrid.__doc__.strip(),
           "grid": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
           "selected": {k: best[k] for k in ("k", "w_dense", "w_keyword", "depth")}}
    (RESULTS / "hybrid_tuning_dev.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
    print("selected:", out["selected"])


# ------------------------------------------------------------- audit sheets
def _coherence_positions(pages: list[DocumentPage], n: int = 10) -> list[dict]:
    """Rule fixed before labelling: n pages evenly spaced over the sorted content
    pages; anchor = 60 characters starting at the first word after the page midpoint."""
    ordered = sorted(pages, key=lambda p: (p.document_id, p.page))
    picks = [ordered[round(i * (len(ordered) - 1) / (n - 1))] for i in range(n)]
    out = []
    for i, p in enumerate(picks, 1):
        flat = " ".join(p.text.split())
        mid = flat.find(" ", len(flat) // 2) + 1
        out.append({"position_id": f"P{i:02d}", "document_id": p.document_id, "page": p.page,
                    "anchor": flat[mid:mid + 60]})
    return out


def cmd_audit_prep(args) -> None:
    cfg = _load_config()
    pages = [DocumentPage(**d) for d in _read_jsonl(RESULTS / "pages.jsonl")]
    audit = RESULTS / "audit"
    audit.mkdir(parents=True, exist_ok=True)

    # 1) chunk coherence: same 10 positions for each strategy
    rows = []
    for pos in _coherence_positions(pages):
        for name in cfg["eval_strategies"]:
            table = cfg["strategies"][name].get("reuse_chunks_of", name)
            chunks = [c for c in _read_jsonl(RESULTS / f"chunks_{table}.jsonl")
                      if c["document_id"] == pos["document_id"] and c["page"] == pos["page"]]
            key = norm(pos["anchor"][:30])
            match = next((c for c in chunks if key in norm(c["text"])), None)
            rows.append({**pos, "strategy": name,
                         "chunk_id": match["chunk_id"] if match else "NOT_FOUND",
                         "chunk_text": match["text"] if match else ""})
    _write_csv(audit / "coherence_sheet.csv", rows)

    # 2) metadata + 3) grounding on test questions
    traces = _read_jsonl(RESULTS / "traces_test.jsonl")
    page_meta = {(p.document_id, p.page): p.metadata for p in pages}
    meta_rows, ground_rows = [], []
    for t in traces:
        for h in t["top3"]:
            src = page_meta.get((h["document_id"], h["page"]), {})
            meta_rows.append({"strategy": t["strategy"], "qid": t["id"], "rank": h["rank"],
                              "filter": json.dumps(t["applied_filter"] or {}),
                              "question_filter": json.dumps(_q_filter(t["id"])),
                              "chunk_id": h["chunk_id"], "page": h["page"],
                              "chunk_chapter": h["metadata"].get("chapter"),
                              "chunk_section": h["metadata"].get("section"),
                              "page_chapter_from_manifest": src.get("chapter"),
                              "chunk_text_head": h["text"][:200]})
        ground_rows.append({"strategy": t["strategy"], "qid": t["id"], "question": t["question"],
                            "expected_answer": t["expected_answer"],
                            "evidence_quote": t["evidence_quote"],
                            "auto_evidence_hit3": t["hit@3"],
                            "top3_text": "\n---\n".join(
                                f"[{h['rank']}] p{h['page']} {h['text']}" for h in t["top3"])})
    _write_csv(audit / "metadata_sheet.csv", meta_rows)
    _write_csv(audit / "grounding_sheet.csv", ground_rows)
    print(f"Wrote audit sheets to {audit}")


_QUESTIONS_CACHE: dict[str, dict] = {}


def _q_filter(qid: str) -> dict | None:
    if not _QUESTIONS_CACHE:
        _QUESTIONS_CACHE.update({q["id"]: q for q in _read_jsonl(QUESTIONS)})
    return _QUESTIONS_CACHE[qid].get("filter")


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ----------------------------------------------------------- audit summary
def cmd_audit_summary(args) -> None:
    """Merge manual labels (results/audit/labels_*.json) into a summary."""
    audit = RESULTS / "audit"
    coh = json.loads((audit / "labels_coherence.json").read_text(encoding="utf-8"))
    meta = json.loads((audit / "labels_metadata.json").read_text(encoding="utf-8"))
    grd = json.loads((audit / "labels_grounding.json").read_text(encoding="utf-8"))
    out: dict = {"labeller": coh.get("labeller"), "strategies": {}}
    for name in sorted({r["strategy"] for r in coh["labels"]}):
        c = [r["score"] for r in coh["labels"] if r["strategy"] == name]
        m = [r for r in meta["labels"] if r["strategy"] == name]
        f = [r for r in meta.get("filter_compliance", []) if r["strategy"] == name]
        g = [r["score"] for r in grd["labels"] if r["strategy"] == name]
        out["strategies"][name] = {
            "coherence_mean_of_2": round(sum(c) / len(c), 2), "coherence_n": len(c),
            "coherence_counts": {s: c.count(s) for s in (0, 1, 2)},
            "metadata_complete_correct": f"{sum(r['complete_and_correct'] for r in m)}/{len(m)}",
            "filter_compliance": (f"{sum(r['top3_compliant'] for r in f)}/{len(f)}" if f else "N/A"),
            "grounding_counts": {s: g.count(s) for s in (0, 1, 2)},
            "grounding_mean_of_2": round(sum(g) / len(g), 2), "grounding_n": len(g),
        }
    (RESULTS / "manual_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                                 encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main() -> None:
    load_dotenv(STARTER / ".env")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest")
    p.add_argument("--pages-only", action="store_true", help="only extract pages (no embedding)")
    p.set_defaults(func=cmd_ingest)
    p = sub.add_parser("retrieve")
    p.add_argument("--split", choices=["dev", "test", "all"], required=True)
    p.add_argument("--strategies", help="comma-separated subset, e.g. D (writes *_D files)")
    p.set_defaults(func=cmd_retrieve)
    sub.add_parser("tune-hybrid", help="grid search RRF params on dev only").set_defaults(
        func=cmd_tune_hybrid)
    sub.add_parser("audit-prep").set_defaults(func=cmd_audit_prep)
    sub.add_parser("audit-summary").set_defaults(func=cmd_audit_summary)
    args = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    args.func(args)


if __name__ == "__main__":
    main()
