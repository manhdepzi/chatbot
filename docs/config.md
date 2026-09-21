# Cấu hình & lệnh

## Biến môi trường (`.env`)

Đọc qua `python-dotenv` trong `src/config.py` (`load_dotenv(ROOT / ".env")`).

| Biến | Mặc định | Mô tả |
|---|---|---|
| `OPENAI_API_KEY` | *(bắt buộc)* | API key OpenAI (chat + embeddings) |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Model chat định giá |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Model embedding cho RAG |

Mẫu `.env` (xem `.env.example`):

```
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini
```

> `.env` chứa key thật — **không commit** (đã có trong `.gitignore`).

## Đường dẫn & hằng số (`src/config.py`)

| Hằng | Giá trị | Ghi chú |
|---|---|---|
| `ROOT` | thư mục gốc dự án | suy từ `src/` |
| `DATA_DIR` | `data/` | BOQ đầu vào |
| `TEMPLATE_DIR` | `report-template/` | mẫu báo giá |
| `GOLDEN_DIR` | `golden-data/` | báo giá vàng |
| `REPORT_DIR` | `reports/` | output |
| `INDEX_DIR` | `rag_index/` | cache index + state |
| `LOG_DIR` | `logs/` | log chạy |
| `CHROMA_COLLECTION` | `golden_quotes` | tên collection |
| `RAG_TOP_K` | `3` | số ví dụ truy vấn mặc định |
| `INPUT_EXTS` | `.xls`, `.xlsx` | extension hợp lệ |
| `IGNORE_PREFIXES` | `~$`, `.` | tiền tố file bỏ qua |

## Cài đặt

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

`requirements.txt`:

```
pandas>=2.0
openpyxl>=3.1
xlrd>=2.0
python-dotenv>=1.0
watchdog>=4.0
openai>=1.0
chromadb>=1.0
```

## Lệnh (Makefile / CLI)

| Make | CLI tương đương | Mô tả |
|---|---|---|
| `make setup` | — | tạo venv + cài dependencies |
| `make build-index` | `python main.py build-index` | (re)build index RAG |
| `make run` | `python main.py run` | xử lý toàn bộ `data/` (bỏ qua file đã có report) |
| `make force` | `python main.py run --force` | xử lý toàn bộ + ghi đè report |
| `make run-file FILE=…` | `python main.py run --file …` | xử lý 1 file |
| `make watch` | `python main.py watch` | watcher nền |
| `make clean` | — | xoá `reports/*.xlsx` + `rag_index/` |

### Tham số `run`

| Cờ | Mô tả |
|---|---|
| `--file PATH` | xử lý đúng 1 file (không có → xử lý cả `data/`) |
| `--force` | ghi đè report đã tồn tại |
| `--top-k N` | ghi đè số ví dụ truy vấn (mặc định `RAG_TOP_K=3`) |
| `--rebuild-index` | build lại index trước khi xử lý file |

## Luồng chạy điển hình

```bash
# lần đầu / khi thêm báo giá vàng mới
make build-index

# sinh báo giá cho toàn bộ BOQ trong data/
make run

# hoặc chạy watcher để tự động xử lý file mới
make watch
```

## Logging

Log ghi ra cả stdout lẫn `logs/rag-report.log` (encoding UTF-8). Các logger nhiễu
của thư viện (`httpx`, `openai`, `chromadb`, `opentelemetry`) bị đặt về `WARNING`
trong `src/config.py`.

## Tham chiếu chéo

- [architecture.md](architecture.md) — tổng quan.
- [modules.md](modules.md) — chi tiết từng module.
- [rag.md](rag.md) — build index & truy vấn.
- [pricing.md](pricing.md) — công thức tính giá.
