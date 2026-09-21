# Hệ thống RAG sinh báo giá Kaiyo Việt Nam

Tự động tạo **báo giá xây lắp** (file Excel) từ các BOQ đầu vào trong `data/`,
sử dụng RAG (học từ `golden-data/`, lưu vào **vector database ChromaDB**) + LLM
(OpenAI) để phân loại sản phẩm và định giá, theo mẫu chuẩn trong `report-template/`.

## Cài đặt

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Cấu hình trong `.env`:

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.4-mini
```

## Sử dụng

```bash
# 1) (Re)build RAG index từ golden-data (lần đầu hoặc khi thêm golden mới)
./.venv/bin/python main.py build-index

# 2) Chạy 1 lần cho toàn bộ file trong data/
./.venv/bin/python main.py run

# Chạy 1 file cụ thể (ghi đè nếu đã có)
./.venv/bin/python main.py run --file data/BTN1205926.xlsx --force

# 3) Chạy watcher nền: tự sinh report khi có file Excel mới xuất hiện trong data/
./.venv/bin/python main.py watch
```

Output được ghi vào `reports/<tên-file>.xlsx`. Mỗi file đầu vào trong `data/`
sẽ có một báo cáo tương ứng.

## Cách hoạt động

```
data/<file>.xlsx
   → input_parser   : chuẩn hóa thành danh sách sản phẩm + header báo giá
   → retriever      : tìm sản phẩm tương tự trong vector DB ChromaDB (embedding cosine)
   → LLM            : phân loại mã SP + ước tính đơn giá / hệ số / giá tôn
   → calculator     : diện tích, thành tiền, tổng trước/sau thuế (số học chính xác)
   → report_builder : điền template → reports/<file>.xlsx
```

**Ưu tiên exact-match:** nếu một sản phẩm xuất hiện nguyên văn trong báo giá
vàng, hệ thống dùng nguyên trường của nó (kể cả khi giá để trống) thay vì để
LLM "bịa" giá từ sản phẩm gần giống.

## Cấu trúc thư mục

```
data/              # BOQ đầu vào (watch)
report-template/   # mẫu báo giá chuẩn
golden-data/       # báo giá vàng — tri thức RAG
reports/           # output
rag_index/         # vector DB ChromaDB + state (state.json)
logs/              # log chạy
src/               # code
  loaders/         # đọc + parse Excel
  rag/             # golden parser, embeddings, index, retriever
  llm/             # OpenAI client + prompts
  pricing/         # số học giá
  generator/       # điền template
  pipeline.py      # orchestration
  watcher.py       # theo dõi data/
  cli.py           # lệnh
```

## Lưu ý

- **Thư mục output** là `reports/` (đổi tên trong `src/config.py` → `REPORT_DIR`
  nếu muốn dùng `report/`).
- File `.xls` cũ được đọc qua `xlrd`, `.xlsx` qua `openpyxl`.
- Độ chính xác giá phụ thuộc vào mức khớp với `golden-data/`. Mỗi lần thêm báo
  giá vàng mới, chạy lại `build-index` để hệ thống học thêm.
