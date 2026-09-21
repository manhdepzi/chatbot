# Kiến trúc hệ thống

## Mục tiêu

Từ một file BOQ đầu vào (`data/*.xls` / `*.xlsx`) chứa danh sách vật tư cần báo giá,
hệ thống tự sinh ra một file báo giá hoàn chỉnh (`reports/<tên>.xlsx`) theo mẫu chuẩn
Kaiyo, trong đó:

- mỗi dòng sản phẩm được **phân loại mã SP** và **định giá đơn giá**,
- số học (diện tích, thành tiền, tổng trước/sau thuế) được tính **chính xác, không qua LLM**.

## Nguyên tắc thiết kế

1. **LLM chỉ quyết định, không tính toán.** Model phân loại mã SP và ước tính đơn giá /
   hệ số giá; mọi phép nhân, diện tích, tổng đều do `src/pricing/` tính bằng số học
   thuần để tránh sai số.
2. **Exact-match thắng.** Nếu một sản phẩm xuất hiện nguyên văn trong báo giá vàng,
   hệ thống dùng nguyên trường của nó (kể cả giá để trống) và bỏ qua LLM — không để
   model "bịa" giá từ sản phẩm gần giống.
3. **RAG làm nguồn tri thức.** Toàn bộ kinh nghiệm định giá nằm trong `golden-data/`,
   được vector-hoá vào ChromaDB để truy vấn theo độ tương đồng cosine.
4. **Idempotent & traceable.** Kết quả mỗi file được lưu state (`rag_index/state.json`),
   index được build với ID ổn định theo (source + tên), nên rebuild không đổi.

## Sơ đồ thành phần

```
┌─────────────────────────────────────────────────────────────────────┐
│  CLI / main.py / Makefile                                            │
│     run · run --file · watch · build-index                           │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  src/loaders                                                        │
│     excel_reader.py   → đọc .xls/.xlsx thành grid 2D                │
│     input_parser.py   → grid → {header, items[]}                    │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ items (chưa có giá)
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  src/pipeline.py  (orchestrator)                                    │
│     với mỗi item:                                                    │
│       _fill_dims_from_name  (bù kích thước từ tên SP)               │
│       exact_match? ──► dùng giá vàng nguyên văn, bỏ qua LLM          │
│       else retrieve top-k + chat_json(LLM) → pricing metadata        │
│       compute_area + thanh_tien (số học)                             │
└───────┬──────────────────────┬───────────────────────┬──────────────┘
        │                      │                       │
        ▼                      ▼                       ▼
┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
│ src/rag/     │     │ src/llm/         │     │ src/pricing/     │
│ index.py     │     │ client.py        │     │ calculator.py    │
│ retriever.py │     │ prompts.py       │     │ (công thức diện  │
│ golden_pars… │     │ (OpenAI chat +   │     │  tích, thanh_tien│
│ ChromaDB     │     │  embeddings)     │     │  totals)         │
└──────────────┘     └──────────────────┘     └──────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  src/generator/report_builder.py                                     │
│     điền template → reports/<tên>.xlsx                               │
└─────────────────────────────────────────────────────────────────────┘
```

## Luồng dữ liệu chi tiết

1. **Đọc input** — `excel_reader.read_excel(path)` mở file, trả về `list[sheet]`,
   mỗi sheet là `{"name": str, "grid": list[list[value]]}` (đã pad đều độ rộng cột,
   NaN → None, chuỗi được trim).

2. **Parse input** — `input_parser.parse_input(path)`:
   - tìm header row (chứa "STT" + cột tên),
   - trích từng dòng thành item chuẩn hoá (`ten`, `don_vi`, `khoi_luong`, `ma_sp`,
     `vat_lieu`, `xuat_xu`, `ghi_chu` + các cột kích thước `w1/h1/…/l/r/e/area`),
   - nhận diện dòng **section** (tiêu đề không có STT số) giữ nguyên trong output,
   - trích header báo giá (Kính Gửi, Số BG, Ngày BG, Dự Án, …),
   - nếu nhiều sheet: chọn sheet có nhiều dòng có khối lượng nhất làm sheet chính,
     rồi **merge các sheet khác theo STT** (bù trường thiếu).

3. **Build/load index** — `rag.index.ensure()`: load ChromaDB nếu có sẵn, ngược lại
   build từ `golden-data/` (xem [rag.md](rag.md)).

4. **Định giá từng item** — `pipeline._price_item()` (xem [pricing.md](pricing.md)):
   - bù kích thước từ tên sản phẩm,
   - exact-match (ưu tiên) hoặc retrieve top-k → LLM,
   - `compute_area` + `thanh_tien`.

5. **Điền template** — `report_builder.build_report()`: copy `report-template/
   kaiyo_quotation_template.xlsx`, tự dò vị trí cột từ header của template (nên code
   không phụ thuộc vị trí cột cứng), xoá dữ liệu cũ, ghi item + tổng + header.

6. **Lưu state** — `pipeline._save_state()` ghi `rag_index/state.json` để debug và
   đảm bảo idempotency.

## Thư mục

| Thư mục | Vai trò | Commit? |
|---|---|---|
| `src/` | toàn bộ code | ✅ |
| `report-template/` | mẫu báo giá chuẩn (bắt buộc) | ✅ |
| `docs/` | tài liệu kiến trúc | ✅ |
| `data/` | BOQ đầu vào | ❌ (dữ liệu khách hàng) |
| `golden-data/` | báo giá vàng — tri thức RAG | ❌ (dữ liệu khách hàng) |
| `reports/` | output | ❌ (tái sinh) |
| `rag_index/` | cache index + state | ❌ (tái sinh) |
| `logs/` | log chạy | ❌ |

## Tham chiếu chéo

- [modules.md](modules.md) — chi tiết từng module.
- [rag.md](rag.md) — cơ chế RAG.
- [pricing.md](pricing.md) — công thức tính.
- [config.md](config.md) — cấu hình & lệnh.
