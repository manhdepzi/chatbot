# Hệ thống RAG (golden-data → ChromaDB → truy vấn)

## Tổng quan

Tri thức định giá của hệ thống nằm trong **`golden-data/`** — các báo giá vàng
(file Excel hoàn chỉnh, chứa giá thật từng sản phẩm). Khi `build-index` chạy,
hệ thống:

1. trích từng dòng có tên + giá thành **document**,
2. embed tên sản phẩm thành vector (OpenAI `text-embedding-3-small`),
3. lưu vào **ChromaDB** (persistent) kèm metadata đầy đủ,
4. đồng thời lưu bản sao raw (`rag_index/index.json`) để truy vấn lossless.

## Quy trình build index

```
golden-data/*.xlsx
   │  golden_parser.parse_golden_dir()
   ▼
list[doc]  {text, meta, source, priced}
   │  index.build()
   ▼
ChromaDB collection "golden_quotes" (cosine)  +  rag_index/index.json
```

Các hàm trong `rag/index.py`:

| Hàm | Mô tả |
|---|---|
| `build()` | xoá collection cũ, embed + thêm docs theo batch (`_BATCH=64`), lưu index.json |
| `load()` | trả về handle `{collection, docs}` hoặc `None` nếu store rỗng |
| `ensure(force)` | `load()` nếu có; ngược lại `build()` |
| `search(index, vec, top_k, priced_only)` | truy vấn top-k theo cosine |
| `_doc_id()` | ID ổn định = SHA1(source + text) + seq → rebuild idempotent |

## Document

Mỗi dòng báo giá vàng → một document:

```python
{
  "text":   "Ống tôn mạ kẽm 1800x500L1110mm",   # trường để embed / truy vấn
  "meta":   {"stt", "don_vi", "khoi_luong", "don_gia", "ma_sp",
             "vat_lieu", "xuat_xu", "ghi_chu", "gia_ton", "he_so",
             "ty_ren", "w1", "h1", ...},         # metadata đầy đủ
  "source": "06- báo giá -THKL TAHK 1.xlsx:BG",  # truy vết nguồn
  "priced": True | False                         # có đơn giá / giá tôn?
}
```

- `priced = True` nếu `meta` có `don_gia` **hoặc** `gia_ton`.
- Document **không giá** vẫn được giữ (dạy phân loại mã SP) nhưng đánh dấu
  `priced=False`, và bị loại khi truy vấn `priced_only=True`.

## Truy vấn

`retriever.py` cung cấp hai lối:

1. **Exact-match** — `exact_match(name, index)`:
   - chuẩn hoá tên (`" ".join(split()).lower()`),
   - duyệt toàn bộ docs trong `index.json`, trả doc có `text` trùng chính xác,
   - ưu tiên doc có giá hơn doc không giá.
   - Khi khớp → dùng nguyên trường (kể cả giá trống), **bỏ qua LLM**.

2. **Semantic top-k** — `retrieve(name, index, top_k)`:
   - embed tên SP, gọi `search(priced_only=True)`.
   - `search` fetch `top_k * 4` kết quả rồi lọc, để sau khi loại doc không giá vẫn
     đủ `top_k`.
   - score = `1 - distance` (ChromaDB dùng metric cosine).

## ChromaDB

- Client: `chromadb.PersistentClient(path=rag_index/chroma)`.
- Collection: `golden_quotes`, `metadata={"hnsw:space": "cosine"}`.
- Metadata phải "phẳng" (str/int/float/bool) nên các giá trị phức tạp được
  `json.dumps` (`_flat_meta`), và giải mã lại khi đọc (`_unflat_meta`).

## Thêm báo giá vàng mới

1. Copy file Excel vào `golden-data/` (đảm bảo có sheet chứa header STT + tên +
   cột giá như các file cũ).
2. Chạy lại index:
   ```bash
   make build-index
   # hoặc
   ./.venv/bin/python main.py build-index
   ```
3. Hệ thống sẽ xoá collection cũ và rebuild toàn bộ (ID ổn định nên không trùng lặp).

## Lưu ý

- `golden-data/` và `data/` **không được commit** lên Git (chứa giá & tên khách
  hàng thật) — xem [architecture.md](architecture.md).
- `rag_index/` là cache, có thể xoá và rebuild bằng `make clean` rồi
  `make build-index`.
