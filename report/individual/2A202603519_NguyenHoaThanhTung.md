# Báo cáo cá nhân - Day 6

> Học viên: Nguyễn Hoa Thanh Tùng · MSSV 2A202603519 · Lớp L3A · Ngày chạy: 2026-11-13 (UTC+7)
>
> **Lưu ý về nhãn thủ công:** toàn bộ nhãn coherence, metadata và grounding trong báo cáo này do **học viên (tác giả) tự chấm**, kèm lý do từng dòng trong `results/audit/labels_*.json`. Người chấm nhãn cũng là người viết pipeline nên có nguy cơ thiên lệch (ví dụ thiên về chiến lược B).

## Dữ liệu và pipeline

- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang:**
  - Giáo trình OpenStax *Introduction to Python Programming* (Das, Lawson, Mayfield, Norouzi; ISBN 978-1-961584-45-7; xuất bản 2024). Giấy phép **CC BY-NC-SA 4.0**, theo trang bản quyền (trang PDF 4). Bài lab là phi thương mại; text trích ra giữ cùng giấy phép và ghi nguồn.
  - PDF có 397 trang. Corpus dùng **chương 1 Statements, 2 Expressions, 3 Objects = trang PDF 17-94** (78 trang vật lý).
  - Quy tắc đếm: mọi trang trong khoảng của 3 chương; trang nào sau khi bỏ header/footer còn < 300 ký tự thì bị loại và ghi log. Kết quả: **77 trang nội dung**, 109.708 ký tự; trang 46 bị loại vì rỗng. Danh sách từng trang và số ký tự: `data/page_index.json`.
  - Manifest kèm URL tải, SHA256 và kích thước: `data/manifest.json`. Không tính: bìa, mục lục, lời nói đầu, đáp án, index.
- **Chuẩn hóa, lỗi và giới hạn:**
  - Đọc bằng pypdf 6.21.4, chuẩn hóa Unicode NFC, thay ligature, bỏ soft hyphen.
  - Bỏ 4 dạng header/footer lặp lại (ví dụ `Access for free at openstax.org`, `10 1 • Statements`). Đã kiểm tra: còn 0 dòng footer.
  - Giữ xuống dòng để phục vụ chunking theo đoạn.
  - Lỗi được ghi ở `results/ingest_errors.jsonl` (1 trang rỗng).
  - Giới hạn:
    - Bảng biểu bị trích thành text phẳng, mất cấu trúc cột (ví dụ bảng 2.4, 3.2).
    - Công thức toán dạng ảnh bị mất (trang 56-58, 65 có chỗ trống như "values are and").
    - Từ bị ngắt bằng gạch nối cuối dòng không được nối lại (ví dụ "read-eval- print").
    - Không cần OCR vì mọi trang đều có lớp text.
- **Embedding, vector store, lệnh chạy và chi phí:**
  - Model `intfloat/multilingual-e5-small` chạy local qua sentence-transformers 6.3.0 + torch 2.15.0+cpu. 384 chiều, prefix `passage: ` cho chunk và `query: ` cho câu hỏi, vector được normalize. Cùng một model cho cả 3 cấu hình.
  - Vector store: PostgreSQL 16.16 + **pgvector 0.8.7** (Docker), một bảng `chunks` có cột `strategy`. Metric cosine (`<=>`), **exact search** (không dùng index ANN để filter không làm hụt top-k).
  - Lệnh chạy: xem `README.md` (`python -m src.run ingest | retrieve --split dev|test | audit-prep | audit-summary`).
  - Chi phí: **0 USD** (CPU local). Thời gian embed khoảng 9 giây mỗi chiến lược.
- **Metadata:**
  - Bắt buộc: `document_id` (`openstax-intro-python`), `source` (URL trang sách OpenStax), `page` (trang PDF vật lý, = trang in + 10).
  - **Trường filter: `chapter`** (số nguyên 1-3). Giá trị lấy từ bookmark (outline) của PDF; mỗi chương bắt đầu ở trang mới nên giá trị chính xác theo trang.
  - Các trường phụ:
    - `chapter_title`.
    - `section`: bookmark mục số gần nhất cùng chương. Đây là giá trị **gần đúng** vì mục thường bắt đầu giữa trang.
    - `document_type`, `language`.
    - `char_start` (A) và `heading` (B).
  - Lý do chọn `chapter`: ba chương trùng chủ đề (len() cho chuỗi ở chương 1 và cho list ở chương 3; nối chuỗi ở chương 1 và 2; type() ở chương 1 và 3). Người học thường biết mình đang học chương nào, nên chương là ngữ cảnh lọc tự nhiên.

## Cấu hình và benchmark

- **A / B / C** (tham số ở `starter/configs/strategies.json`):
  - **A**: cửa sổ cố định 500 ký tự, overlap 50, gộp khoảng trắng; không vượt ranh giới trang; không filter. 272 chunk.
  - **B**: theo đoạn/heading. Heading là các nhãn khối của OpenStax (`1.2 ...`, `Learning objectives`, `CHECKPOINT`, `CONCEPTS IN PRACTICE`, `EXAMPLE`, `TRY IT`). Đoạn kết thúc khi dòng hết câu và ngắn hơn 80% bề rộng dòng thường. Gộp đến tối đa 1000 ký tự; đoạn quá dài tách theo câu; mảnh < 200 ký tự được gộp vào chunk liền kề. Không vượt trang; không filter. 226 chunk.
  - **C**: **dùng lại đúng chunk và vector của B**, chỉ thêm `WHERE metadata->>'chapter' = ?` cho câu có filter. Câu không có filter chạy y hệt B.
  - Khác biệt duy nhất giữa B và C là filter. A và B khác nhau ở cách chunk.
  - Thay đổi có chủ đích trong quá trình làm, trước khi có benchmark: bản B đầu tiên tạo 40 chunk < 150 ký tự (ví dụ chunk chỉ chứa "TRY IT") và nhận nhầm một dòng output code làm heading. Tôi đã sửa bằng cách gộp mảnh vụn và siết quy tắc heading. Thống kê cũ ở `results/ingest_stats_v1_before_chunk_fix.json`. Lần nạp lại đó xóa 115 chunk lỗi thời của B (`results/reingest_log.jsonl`, dòng 3).
- **Benchmark:**
  - 22 câu (`benchmark/questions.jsonl`): **16 dev / 6 test**, chia và ghi lại (`benchmark/split_note.md`) **trước** lần truy xuất đầu tiên.
  - Loại câu: 6 trực tiếp, 7 diễn đạt lại, 5 điều kiện/ngoại lệ, 4 metadata.
  - **Câu filter**: q15, q16 (dev), q21, q22 (test). Giá trị `chapter` là **ngữ cảnh người dùng giả định, cung cấp trước khi truy xuất** ("tôi đang học chương X"), không phải nguồn gold. Mỗi câu filter đều có đoạn cùng chủ đề ở chương khác để gây nhiễu.
  - Kiểm chứng evidence: mỗi `evidence_quote` được chép từ text đã chuẩn hóa. `starter/scripts/verify_benchmark.py` kiểm tra quote nằm trên **mọi** trang gold, giá trị filter tồn tại và trang gold thỏa filter. Kết quả: `OK: all evidence verified`.
  - Riêng q13 chấp nhận 2 trang gold (57 hoặc 74), mỗi trang có evidence riêng.
- **Tiêu chí chọn chiến lược, chốt trước khi xem test:** trên dev, ưu tiên (1) Evidence Hit@3, (2) Hit@1, (3) coherence trung bình, (4) ít chunk/thời gian ingest.
  - **Lưu ý trung thực:** B và C hòa ở cả 4 tiêu chí trên dev. Tôi thêm tiêu chí phụ "tuân thủ filter" **sau khi xem dev nhưng trước khi chạy test**: ở q16, top-3 của B có 2 chunk chương 1, còn C nằm trọn trong chương 2.
  - Lựa chọn C được ghi vào `artifacts/experiment_log.csv` trước khi chạy test. Test chỉ chạy **một lần**, không chỉnh gì sau đó.

## Kết quả

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 9/16 | 14/16 | 272 | embed 8,85 s; search ~52 ms/câu | 0 |
| B | dev | 13/16 | 14/16 | 226 | embed 8,60 s; search ~53 ms/câu | 0 |
| C | dev | 13/16 | 14/16 | 226 (dùng lại B) | không embed thêm; search ~50 ms/câu | 0 |
| A | test | 2/6 | 6/6 | 272 | search ~53 ms/câu | 0 |
| B | test | 4/6 | 6/6 | 226 | search ~54 ms/câu | 0 |
| C | test | **5/6** | 6/6 | 226 | search ~56 ms/câu | 0 |

Đọc PDF: 2,0 s cho 77 trang. Thời gian đo trên CPU laptop, một lần chạy, nên chỉ mang tính tham khảo. Search time gồm cả kết nối DB.

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | 0,8 (10) - 0:2, 1:8, 2:0 | 18/18 | test 1/2 · dev 0/2 | 0/0/6 - 2,0 |
| B | 1,9 (10) - 0:0, 1:1, 2:9 | 18/18 | test 1/2 · dev 1/2 | 0/0/6 - 2,0 |
| C | 1,9 (10) - giống B | 18/18 | test 2/2 · dev 2/2 | 0/0/6 - 2,0 |

- Metadata: kiểm tra 6 câu test × top-3 = 18 bản ghi mỗi chiến lược, xét `source`, `page`, `chapter`. Trường `section` được kiểm riêng vì đã ghi là gần đúng: **4/54 bản ghi sai section** (p77 bị gắn "3.1" dù là phần mở đầu chương 3; p82 bị gắn "3.2" dù là bài tập của mục 3.1).
- Filter tuân thủ là số câu có filter mà cả 3 chunk top-3 đều thuộc chương yêu cầu. A và B không áp filter; số liệu của chúng cho thấy mức "lạc phạm vi".

**Đường dẫn và cách chọn mẫu:**
- Trace đầy đủ: `results/traces_dev.jsonl`, `results/traces_test.jsonl` (câu hỏi, top-3 chunk id/text/score/metadata, nhãn hit, filter đã áp).
- Chỉ số: `results/summary_*.json`.
- Sheet chấm: `results/audit/*_sheet.csv`. Nhãn kèm lý do: `results/audit/labels_*.json`. Tổng hợp: `results/manual_summary.json`.
- Coherence: 10 vị trí cố định trước khi chấm (trang cách đều trong 77 trang, mốc là 60 ký tự sau giữa trang; `_coherence_positions` trong `src/run.py`). C dùng chunk của B nên nhãn giống B. Vị trí P09 của B được ghép thủ công vì mốc nằm vắt qua ranh giới 2 chunk.

## Phân tích và kết luận

- **So sánh A-B (đổi chunking):**
  - Hit@3 như nhau (dev 14/16, test 6/6), nhưng **Hit@1 của B tốt hơn rõ** (dev 13 vs 9, test 4 vs 2).
  - Coherence 1,9 vs 0,8: chunk A gần như luôn bị cắt giữa từ ở hai đầu (8/10 vị trí điểm 1, 2/10 điểm 0). B giữ trọn đoạn hoặc khối "CONCEPTS IN PRACTICE".
  - A có 1 câu evidence bị tách (q05: trang gold có trong top-3 nhưng không chunk nào chứa đủ câu evidence 150 ký tự).
  - B ít chunk hơn (226 vs 272), ít ký tự hơn (109.559 vs 119.400 vì không overlap), thời gian embed tương đương.
  - Đánh đổi: chunk B dài hơn (đến 993 ký tự), nên một ý nhỏ có thể bị "loãng" giữa nhiều nội dung khác.
- **So sánh B-C (chỉ đổi filter):**
  - Chỉ 2 câu dev và 2 câu test có filter, nên khác biệt chỉ thể hiện ở đó.
  - Test: C thắng q21 (Hit@1). Không filter, top-1 là quiz chương 3 về id()/type() (p87); có filter chương 1, bảng tham chiếu p43 lên hạng 1.
  - Dev: q15 không đổi (cả B và C đều tìm đúng p89).
  - Filter tuân thủ: C 4/4, B 2/4.
  - Chi phí: gần như 0 (thêm một điều kiện SQL, không embed lại).
  - Đánh đổi: C chỉ có ích khi người dùng biết và cung cấp đúng chương. Filter sai sẽ loại hẳn đáp án đúng.
- **Ví dụ lỗi:**
  1. **q16 (dev, gold p69, sai ở cả A/B/C).** Câu hỏi: "+ có cần để nối các string literal đặt cạnh nhau không?". Câu evidence "Python automatically concatenates multiple strings. The + operator is not required" nằm **cuối** chunk B `p69:1`, sau khoảng 650 ký tự trích Tuyên ngôn Độc lập. Vector của chunk bị nội dung lạc đề chi phối nên không lọt top-3, kể cả khi C đã lọc đúng chương 2. Top-3 của C là p54, p62, p74. Ở A, câu evidence nằm trong chunk `p69:3` cùng với quiz, cũng không lên top-3. Không filter, top-1 của A/B là p28 (nối chuỗi bằng + ở chương 1) - đúng loại nhiễu mà filter định xử lý, nhưng filter không cứu được vì vấn đề nằm ở chunk.
  2. **q11 (dev, gold p51, B và C sai, A đúng ở hạng 2).** Chunk B `p51:1` chứa đủ evidence "int() ... Any fractional part is removed", nhưng gộp cả int(), float(), str() và nhãn video "Ordering pizza", nên bị chunk quiz cùng trang `p51:0` và các trang p55, p74 vượt lên. Chunk A 500 ký tự hẹp hơn, tập trung vào int() nên khớp tốt hơn. Đây là trường hợp chunk lớn hơn làm hại truy xuất.
- **Chiến lược chọn và điều kiện áp dụng:** chọn **C** (chunk theo đoạn/heading + filter `chapter` khi người dùng cung cấp chương). B là phương án tương đương khi không có ngữ cảnh chương. Với tài liệu kiểu giáo trình có cấu trúc khối rõ, chunk theo cấu trúc cho top-1 và độ mạch lạc tốt hơn hẳn chunk cố định với cùng chi phí. Filter chỉ nên dùng khi giá trị lọc đáng tin, đến từ người dùng hoặc giao diện.
- **Hạn chế:**
  - **Mẫu nhỏ:** test chỉ 6 câu, nên 1 câu = 16,7 điểm phần trăm. Khác biệt Hit@1 B-C trên test là đúng 1 câu (q21). Không thể kết luận C tốt hơn B một cách tổng quát.
  - **Test dễ:** cả 3 chiến lược đạt Evidence Hit@3 6/6 và grounding 2/2 cho mọi câu (hiệu ứng trần), nên grounding không phân biệt được chiến lược. Câu hỏi do cùng một người viết từ cùng cuốn sách, nên test không độc lập hoàn toàn với dev.
  - **Nhãn:** coherence, metadata, grounding do chính tác giả chấm và chưa có người thứ hai chấm độc lập.
  - **Metric tự động:** Evidence Hit dùng khớp chuỗi sau chuẩn hóa nên có thể bỏ sót bằng chứng tương đương diễn đạt khác.
  - **Section:** metadata `section` gần đúng (4/54 sai); không dùng làm filter.
  - **Thời gian:** đo một lần trên máy cá nhân. Chi phí 0 vì chạy local.
- **Nếu làm lại hoặc mở rộng:**
  - Gắn `section` theo vị trí heading trong text thay vì theo trang bookmark, để có thể lọc ở mức mục.
  - Tách đoạn trích code/văn bản dài (như Tuyên ngôn ở p69) ra khỏi câu giải thích, hoặc thêm heading vào đầu chunk, để tránh loãng vector (lỗi q16).
  - Viết thêm câu test khó hơn (diễn đạt xa hơn, nhiều câu filter hơn) và nhờ người thứ hai chấm nhãn. Với hybrid (mục dưới), cần một tập test mới, chưa từng xem, mới kết luận được.

## Mở rộng tùy chọn: hybrid retrieval (D)

Làm theo mục "Mở rộng tùy chọn" của GUIDE, **sau khi** đã chạy test A/B/C.

- **Cách làm:**
  - Giữ nguyên chunk của B và filter `chapter` của C.
  - Bên từ khóa: cột `tsv = to_tsvector('english', text)` (generated column + GIN index) trong cùng bảng pgvector. Câu hỏi được tách thành các lexeme tiếng Anh (bỏ stop word, stem) nối bằng OR, xếp hạng bằng `ts_rank_cd`. Đây là xếp hạng kiểu BM25 của PostgreSQL, không phải BM25 chuẩn.
  - Trộn hai danh sách top-20 (dense và từ khóa) bằng weighted Reciprocal Rank Fusion: `w_dense/(k + rank_dense) + w_keyword/(k + rank_keyword)`.
- **Chọn tham số chỉ trên dev:** quy tắc ghi trước trong `benchmark/split_note.md`. Grid gồm k ∈ {10, 30, 60} × w_keyword ∈ {0.5, 1, 2} (`results/hybrid_tuning_dev.json`).
  - Cả 3 cấu hình w_keyword = 0.5 cùng đạt Hit@1 12/16, Hit@3 15/16; chọn k = 60 theo quy tắc phân định.
  - Trọng số từ khóa càng cao càng tệ: w = 2 chỉ còn 13/16 Hit@3 vì làm mất q15.
  - **Không cấu hình nào giữ được Hit@1 13/16 của C.**
- **So với baseline C** (test chạy D đúng 1 lần, `results/traces_test_D.jsonl`):

| Strategy | Dev Hit@1 | Dev Evidence Hit@3 | Test Hit@1 | Test Evidence Hit@3 | Search trung bình |
|---|---|---|---|---|---|
| C | 13/16 | 14/16 | 5/6 | 6/6 | ~50-56 ms |
| D (C + hybrid) | 12/16 | **15/16** | 4/6 | 6/6 | ~109 ms |

- **D tốt hơn:** **q16** (dev). Câu evidence bị loãng trong chunk p69 dài (lỗi đã phân tích ở trên). Chunk có câu này đứng hạng dense 4 và hạng từ khóa 9; RRF đưa nó vào top-3 (hạng 2). Đúng loại lỗi mà bên từ khóa có thể bù cho bên dense.
- **D tệ hơn (đều mất Hit@1, vẫn giữ Hit@3):**
  - **q01** (dev, "end option of print()"): chunk đúng là dense hạng 1 nhưng từ khóa hạng 6. Một chunk quiz "The print() function..." lặp lại nhiều lần các từ print, output, line, xếp từ khóa hạng 1 và vượt lên.
  - **q19** (test, tuple có sửa được không): chunk đúng p92 là dense hạng 1 nhưng từ khóa chỉ hạng 12 (câu evidence ngắn, ít lặp từ "tuple"). Chunk "Creating a tuple from a list" lặp "tuple"/"list" nhiều lần nên lên hạng 1; chunk đúng tụt xuống hạng 3.
  - Kiểu lỗi chung: trong giáo trình có nhiều quiz và bài tập lặp lại từ khóa của câu hỏi, nên điểm từ khóa ưu tiên các khối này hơn đoạn giải thích.
- **Tài nguyên:** không cần embed lại; thêm một cột generated và index GIN; thời gian search gần gấp đôi (2 truy vấn + trộn trong Python).
- **Kết luận:**
  - Nếu chỉ áp quy tắc chọn trên dev (ưu tiên Hit@3), D xếp trên C (15 vs 14), nhưng phải đổi 1 câu Hit@1.
  - Trên test, D kém C đúng 1 câu ở Hit@1, và Hit@3 bằng nhau (cả hai 6/6, test đã chạm trần).
  - Cả hai chiều đều chỉ chênh **1 câu**, nằm trong sai số của mẫu nhỏ, nên **không đủ bằng chứng** để nói hybrid tốt hơn hay kém hơn.
  - **Giữ C là chiến lược chính**, vì C đã được chốt trước khi có D. Tôi không đổi lựa chọn dựa trên kết quả test.
  - Lưu ý: 6 câu test chưa từng được dùng để chỉnh tham số D, nhưng đã được xem ở lần chạy A/B/C, nên đây không phải holdout mới hoàn toàn.
  - Hybrid đáng thử tiếp nếu: dùng BM25 chuẩn, giảm ảnh hưởng của khối quiz (ví dụ gắn nhãn loại khối và hạ trọng số), và đánh giá trên tập test mới.

## Công cụ AI đã dùng

- Claude Code (Claude, model claude-opus-5-5) dùng để: viết code pipeline trong `starter/src/`.