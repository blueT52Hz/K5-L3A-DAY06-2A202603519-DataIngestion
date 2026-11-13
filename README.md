# Day 06 · Data Ingestion Engineering - bài nộp 2A202603519

Pipeline biến 3 chương giáo trình OpenStax *Introduction to Python Programming* thành kho vector trong PostgreSQL + pgvector, rồi so sánh 3 chiến lược dữ liệu (A fixed-size, B paragraph/heading, C = B + metadata filter) trên 22 câu hỏi (16 dev / 6 test).

- Báo cáo: [report/individual/2A202603519_NguyenHoaThanhTung.md](report/individual/2A202603519_NguyenHoaThanhTung.md)
- Đề bài gốc: [GUIDE.md](GUIDE.md), [BENCHMARK.md](BENCHMARK.md), [RUBRIC.md](RUBRIC.md), [SUBMISSION.md](SUBMISSION.md), [starter/README.md](starter/README.md)

## Kết quả chính

| Strategy | Dev Hit@1 | Dev Evidence Hit@3 | Test Hit@1 | Test Evidence Hit@3 | Chunks |
|---|---|---|---|---|---|
| A fixed 500/50 | 9/16 | 14/16 | 2/6 | 6/6 | 272 |
| B paragraph ≤1000 | 13/16 | 14/16 | 4/6 | 6/6 | 226 |
| C = B + chapter filter | 13/16 | 14/16 | 5/6 | 6/6 | 226 (dùng lại B) |
| D = C + hybrid BM25/RRF (mở rộng) | 12/16 | 15/16 | 4/6 | 6/6 | 226 (dùng lại B) |

Chiến lược được chọn trên dev (trước khi chạy test): **C**. D là phần mở rộng tùy chọn theo GUIDE, thêm sau khi đã chạy test A/B/C; tham số chọn trên dev, test chạy 1 lần. Chi tiết, nhãn thủ công và giới hạn: xem báo cáo.

## Cấu hình

| Thành phần | Giá trị |
|---|---|
| Corpus | OpenStax *Introduction to Python Programming*, ISBN 978-1-961584-45-7, CC BY-NC-SA 4.0; chương 1-3 = trang PDF 17-94; 77 trang nội dung (trang 46 bị loại vì rỗng) |
| Đọc PDF | pypdf 6.21.4, chuẩn hóa Unicode NFC, bỏ header/footer lặp lại |
| Embedding | `intfloat/multilingual-e5-small` (sentence-transformers 6.3.0, torch 2.15.0+cpu), 384 chiều, prefix `passage: ` / `query: `, normalize, chạy CPU, chi phí 0 |
| Vector store | PostgreSQL 16.16 + pgvector 0.8.7 (Docker `pgvector/pgvector:0.8.7-pg16`), bảng `chunks`, cosine `<=>`, exact search (không index ANN), filter bằng SQL trên JSONB |
| Metadata mỗi chunk | `document_id`, `source`, `page` (trang PDF vật lý), `chapter` (trường filter), `chapter_title`, `section` (gần đúng), `document_type`, `language`, + `char_start` (A) / `heading` (B) |
| Tham số chiến lược | [starter/configs/strategies.json](starter/configs/strategies.json) |
| Python | 3.12.5, Windows 10; toàn bộ package: [results/pip_freeze.txt](results/pip_freeze.txt) |

## Cách chạy lại

Yêu cầu: Docker Desktop, Python 3.11-3.14, mạng để tải model và PDF lần đầu.

```powershell
# 1. Lấy PDF (không commit vì kích thước ~10 MB) và kiểm tra SHA256
New-Item -ItemType Directory -Force data\raw
Invoke-WebRequest https://assets.openstax.org/oscms-prodcms/media/documents/Introduction_to_Python_Programming-WEB.pdf -OutFile data\raw\introduction-python-programming.pdf
(Get-FileHash data\raw\introduction-python-programming.pdf).Hash   # e1298e4f...be383d (xem data/manifest.json)

# 2. Môi trường
cd starter
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install sentence-transformers pgvector numpy
Copy-Item .env.example .env        # rồi đặt EMBEDDING_MODEL=intfloat/multilingual-e5-small

# 3. Database
docker compose -p day6_student_starter up -d --wait
python scripts/check_setup.py

# 4. Pipeline
python -m src.run ingest                  # đọc PDF -> chunk A/B -> embed -> pgvector (chạy lại không nhân đôi)
python scripts/verify_benchmark.py        # kiểm chứng evidence trước khi truy xuất
python -m src.run retrieve --split dev    # chọn chiến lược trên dev
python -m src.run retrieve --split test   # chỉ chạy sau khi đã chốt chiến lược
python -m src.run audit-prep              # tạo sheet chấm tay
python -m src.run audit-summary           # gộp nhãn tay -> results/manual_summary.json

# 5. Mở rộng: hybrid (D)
python -m src.run tune-hybrid                         # grid RRF chỉ trên dev -> results/hybrid_tuning_dev.json
python -m src.run retrieve --split dev --strategies D
python -m src.run retrieve --split test --strategies D   # -> results/traces_test_D.jsonl
```

macOS/Linux: dùng `source .venv/bin/activate`, `cp .env.example .env`, `curl -o` thay cho `Invoke-WebRequest`.

## Cấu trúc file

```text
data/manifest.json             nguồn, giấy phép, SHA256, khoảng trang, quy tắc đếm trang, trường filter
data/page_index.json           77 trang được đếm: số ký tự, chapter, section (sinh tự động)
benchmark/questions.jsonl      22 câu: split, expected_answer, gold (nguồn + trang), evidence_quote, filter
benchmark/split_note.md        cách chia dev/test, kiểm chứng evidence, quy tắc chọn chiến lược
starter/src/                   ingestion, chunking, embeddings, store, evaluation, run
starter/configs/strategies.json  tham số A/B/C
starter/scripts/verify_benchmark.py
artifacts/experiment_log.csv   cấu hình, giả thuyết, Hit@1/Hit@3 dev/test của A/B/C
results/pages.jsonl            text từng trang sau chuẩn hóa (CC BY-NC-SA 4.0, ghi nguồn OpenStax)
results/chunks_A.jsonl, chunks_B.jsonl  toàn bộ chunk
results/ingest_stats.json      số chunk, phân bố độ dài, thời gian, chi phí
results/ingest_stats_v1_before_chunk_fix.json  thống kê B bản đầu (trước khi gộp chunk vụn)
results/ingest_errors.jsonl    trang lỗi/rỗng
results/reingest_log.jsonl     bằng chứng nạp lại không nhân đôi và xóa chunk cũ
results/traces_dev.jsonl, traces_test.jsonl    trace top-3 đầy đủ (text, score, metadata, nhãn hit)
results/summary_dev.json, summary_test.json    Hit@1 / Evidence Hit@3
results/audit/*_sheet.csv      sheet chấm tay (coherence, metadata, grounding)
results/audit/labels_*.json    nhãn tay kèm lý do
results/manual_summary.json    tổng hợp nhãn tay
results/hybrid_tuning_dev.json grid 9 cấu hình RRF trên dev và cấu hình được chọn
results/traces_*_D.jsonl, summary_*_D.json     trace và chỉ số của hybrid D
```

## Dữ liệu và bản quyền

Nội dung sách © 2026 Rice University, OpenStax, giấy phép [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). Access for free at openstax.org. Bài lab chỉ dùng phi thương mại; text trích ra trong `results/` giữ cùng giấy phép. File PDF gốc không được commit (xem `.gitignore`).
