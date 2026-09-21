# Chi tiết từng module

Cấu trúc `src/`:

```
src/
├── cli.py            # phân tích argv, điều phối lệnh
├── config.py         # đường dẫn, hằng số, env
├── pipeline.py       # orchestrator end-to-end
├── watcher.py        # theo dõi data/ và xử lý file mới
├── loaders/
│   ├── excel_reader.py    # đọc Excel → grid 2D
│   └── input_parser.py    # grid → header + items
├── rag/
│   ├── golden_parser.py   # trích dòng có giá từ báo giá vàng
│   ├── index.py           # ChromaDB: build/load/search
│   └── retriever.py       # exact-match + top-k truy vấn
├── llm/
│   ├── client.py          # OpenAI wrapper (chat + embeddings, retry)
│   └── prompts.py         # prompt định giá
├── pricing/
│   └── calculator.py      # diện tích, thanh_tien, totals (số học)
└── generator/
    └── report_builder.py  # điền template → file output
```

## `cli.py`

Entry point dòng lệnh. Dùng `argparse` với subcommand:

| Lệnh | Hàm | Mô tả |
|---|---|---|
| `run` | `cmd_run` | xử lý 1 file (`--file`) hoặc toàn bộ `data/` |
| `build-index` | `cmd_build_index` | (re)build index ChromaDB từ golden-data |
| `watch` | `cmd_watch` | chạy watcher nền |

`_setup_logging()` ghi log ra stdout + `logs/rag-report.log`.

## `config.py`

Điểm định nghĩa tập trung (đọc từ `.env`):

- Đường dẫn: `DATA_DIR`, `TEMPLATE_DIR`, `GOLDEN_DIR`, `REPORT_DIR`,
  `INDEX_DIR`, `LOG_DIR`.
- File: `TEMPLATE_FILE` (`report-template/kaiyo_quotation_template.xlsx`),
  `INDEX_FILE` (`rag_index/index.json`), `STATE_FILE` (`rag_index/state.json`).
- ChromaDB: `CHROMA_DIR`, `CHROMA_COLLECTION = "golden_quotes"`.
- OpenAI: `OPENAI_API_KEY`, `OPENAI_MODEL` (mặc định `gpt-5.4-mini`),
  `EMBEDDING_MODEL` (mặc định `text-embedding-3-small`).
- RAG: `RAG_TOP_K = 3`, `INPUT_EXTS = {".xls", ".xlsx"}`,
  `IGNORE_PREFIXES = ("~$", ".")`.

Ngoài ra tắt log nhiễu của `httpx`/`openai`/`chromadb`/`opentelemetry`.

## `pipeline.py`

Orchestrator chính. Các hàm quan trọng:

- `process_file(path, index, force, top_k)` — xử lý 1 file:
  1. nếu output đã tồn tại và không `--force` → skip,
  2. parse input → `items`,
  3. `_price_item()` từng item,
  4. strip các trường nội bộ bắt đầu bằng `_` (ví dụ `_examples`),
  5. `calc.totals()` + `report_builder.build_report()`,
  6. `_save_state()`.
- `process_all(force, top_k)` — lặp qua mọi file hợp lệ trong `data/`, bắt lỗi
  từng file để một file hỏng không dừng cả batch.
- `_price_item()` — trái tim định giá (xem [pricing.md](pricing.md)).
- `_fill_dims_from_name()` — bù kích thước `w1/h1/w2/…/l/r/e` từ tên SP khi
  bảng diện tích không cung cấp.

## `watcher.py`

Dùng `watchdog` theo dõi `data/` (không đệ quy). File mới (`on_created`/`on_moved`)
được đưa vào hàng đợi `_pending`, sau `DEBOUNCE_SECONDS = 1.5s` mới xử lý để tránh
đọc file chưa ghi xong. Chỉ xử lý file có extension hợp lệ và không bắt đầu bằng
`~$`/`.`.

## `loaders/excel_reader.py`

Chuyển file Excel thành grid thuần:

- `read_xlsx` — dùng `openpyxl` (`data_only=True`, tắt warning).
- `read_xls` — dùng `xlrd` cho file `.xls` cũ.
- `_norm` — trim chuỗi, biến chuỗi rỗng/NaN thành `None`.
- `_pad` — pad đều các hàng theo cột rộng nhất để truy cập cột nhất quán.
- `read_excel` — dispatch theo extension.

## `loaders/input_parser.py`

Xem mô tả ở [architecture.md](architecture.md). Điểm cần lưu ý:

- Bảng alias cột (`_COL_*`) khớp nhiều từ khoá tiếng Việt (`tên công tác`,
  `tên vật tư`, `sản phẩm`, `khối lượng`, `đơn vị`, …).
- `_DIM_COLS` map nhãn cột kích thước (`w1`, `h1`, `l/h`→`l`, `r/d`→`r`,
  `e`, `diện tích /cái`→`area`).
- `_STOP_ROWS` = các dòng kết thúc khối (`tổng cộng`, `viết bằng chữ`, …).
- `_merge_by_stt` merge nhiều sheet **theo STT** (không theo tên, vì cùng một
  sản phẩm có thể hợp lệ ở nhiều mục).

## `rag/golden_parser.py`

Trích dòng có giá từ các file báo giá vàng. Cấu trúc giống `input_parser` nhưng
thêm các cột giá: `đơn giá`, `giá tôn`, `hệ số`, `ty ren`.

- Mỗi dòng → một document `{"text": tên SP, "meta": {...}, "source", "priced"}`.
- `priced = True` nếu có `don_gia` **hoặc** `gia_ton`; dòng không giá vẫn giữ lại
  (dạy phân loại) nhưng đánh dấu `priced=False`.
- `source` ghi `"<tên-file>:<tên-sheet>"` để truy vết.

## `rag/index.py`

Vector store ChromaDB (persistent). Xem [rag.md](rag.md).

## `rag/retriever.py`

- `retrieve(name, index, top_k)` — embed tên SP rồi `search(priced_only=True)`.
- `exact_match(name, index)` — duyệt toàn bộ docs, trả về doc có `text` trùng
  chính xác tên (sau chuẩn hoá), ưu tiên doc có giá.

## `llm/client.py`

Wrapper OpenAI:

- `_get_client()` — singleton, kiểm tra `OPENAI_API_KEY`.
- `_with_retries()` — retry tối đa 4 lần với backoff luỹ thừa, **chỉ** trên lỗi
  `429/500/502/503/504`, các lỗi khác fail nhanh.
- `chat_json()` — gọi `chat.completions` với `response_format={"type": "json_object"}`,
  parse JSON.
- `embed()` — gọi `embeddings.create`, trả về list vector.

## `llm/prompts.py`

`SYSTEM_PROMPT` mô tả chuyên gia định giá Kaiyo + quy tắc phân loại mã SP
(`t` ống, `tb` đầu bịt, `cv` cút, `g` côn thu, `d` zét, `n` chân rẽ, `vt` côn đầu
quạt, `tt` tê). `build_item_prompt()` dựng message gồm item cần định giá + các ví
dụ tương tự (kèm score).

## `pricing/calculator.py`

Số học giá. Xem [pricing.md](pricing.md).

## `generator/report_builder.py`

Điền template output:

- `_find_sheet` — tìm sheet `"Mẫu - xây lắp"`, fallback chọn sheet nhiều dòng nhất.
- `_find_header_row` / `_map_columns` — dò vị trí cột từ header của chính template
  (core match theo substring, dimension match chính xác để tránh nhầm `e`/`r`).
- `build_report` — xoá dữ liệu cũ, chèn thêm dòng nếu thiếu, ghi item + section +
  tổng + header, **copy style** từ dòng mẫu để giữ định dạng.
- `_fill_header` — điền các trường header (Kính Gửi, Số BG, …) kể cả trong cell
  merge (map về anchor trên-trái).
