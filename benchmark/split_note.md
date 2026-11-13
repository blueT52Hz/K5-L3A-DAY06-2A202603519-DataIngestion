# Benchmark - split and selection rule

Frozen on 2026-11-13, **before any retrieval run** (only ingestion and chunk statistics had been run; no query was embedded or searched).

## Questions

- File: `benchmark/questions.jsonl`, 22 questions on OpenStax *Introduction to Python Programming*, chapters 1-3 (physical PDF pages 17-94).
- Split: **16 dev** (q01-q16) / **6 test** (q17-q22).
- Types: `truc_tiep` (direct) 6, `dien_dat_lai` (paraphrase) 7, `dieu_kien` (condition/exception) 5, `metadata` (needs the chapter filter) 4 (counts printed by `verify_benchmark.py`).
- Filter questions: q15, q16 (dev), q21, q22 (test). The filter value (`chapter`) is an **assumed user context given before retrieval** (the learner says which chapter they are studying). It is never the gold source id or the answer. Each filter question has a competing passage on the same topic in another chapter (noted in `filter_context`), so the filter is meaningful.
- For strategy C, questions without a filter run exactly like B (no filter).
- Gold: `gold` lists every accepted (source_id, page). A gold entry can carry its own `evidence_quote` (q13 accepts page 57 or page 74). `gold_source_id` / `gold_page` repeat the same information under the field names used in BENCHMARK.md (`gold_page` is a list when several pages are accepted, only q13: [57, 74]); evaluation code reads `gold`.

## How evidence was verified

1. Text of every content page was extracted and normalized (`results/pages.jsonl`).
2. Each `evidence_quote` was copied from that normalized text and checked by `starter/scripts/verify_benchmark.py`, which asserts the quote appears (after NFKC, lowercase, whitespace collapsing) on every listed gold page and that the filter value exists and is satisfied by the gold page.
3. Each expected answer was checked by reading the gold page, including conditions (q05, q13, q14, q19).

## Split method and leakage risk

- The test set was chosen by topic so most of it covers sections not used by dev questions: chapter 3 strings / f-strings / objects / tuples (q17-q20), plus two filter questions (q21: chapter 1 reference table, q22: chapter 2 mixed data types).
- Leakage risk: dev and test come from the same book and the same writer, and q21/q22 overlap in topic with dev material (type(), int/float), so test is not fully independent. With only 6 test questions, one question = 16.7 percentage points.

## Strategy selection rule (fixed before looking at test)

Choose the strategy on **dev** in this order:

1. Evidence Hit@3 (higher is better)
2. Hit@1
3. Chunk coherence mean (manual, /2)
4. Fewer chunks / lower ingest time

Test is run **once** after the choice is recorded in `artifacts/experiment_log.csv`.

## Optional extension: hybrid retrieval (strategy D), added after the A/B/C test run

Written before any hybrid run (GUIDE "Mở rộng tùy chọn: hybrid retrieval"):

- D keeps the chunks of B and the chapter filter of C, and fuses dense (cosine) with keyword full-text ranking (PostgreSQL `to_tsvector('english')` + `ts_rank_cd`) by weighted Reciprocal Rank Fusion over the top 20 of each list.
- Parameters are chosen on the **16 dev questions only** by `python -m src.run tune-hybrid`. Grid: k in {10, 30, 60} x keyword weight in {0.5, 1.0, 2.0}, dense weight 1.0, depth 20. Rule: highest dev Evidence Hit@3, then Hit@1, then closest to the RRF default (k = 60, weight 1.0).
- D is then compared with the baseline C on the 6 test questions, which were never used to choose D's parameters. Caveat: those test questions were already run and inspected for A/B/C, so this is not a fresh holdout in the strict sense; this is stated in the report.
- Every question where D is worse than C (dev or test) is reported. If anything is changed after seeing test, it will be written in the report and test will no longer be called an independent holdout.
