# Hệ thống RAG sinh báo giá Kaiyo Việt Nam

Tự động tạo **báo giá xây lắp** (file Excel) từ các BOQ đầu vào trong `data/`,
sử dụng RAG (học từ `golden-data/`) + LLM (OpenAI) để phân loại sản phẩm và
định giá, theo mẫu chuẩn trong `report-template/`.

## Cài đặt

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Cấu hình trong `.env`:

```
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.4-mini
# tùy chọn: CHUNK_ROWS=120  RESULT_BATCH=5  LLM_WORKERS=4
```

> Khuyến nghị `OPENAI_MODEL=gpt-5.4` cho chất lượng định giá: bản `mini` hay
> mượn giá của sản phẩm khác chủng loại (VD ống gió mềm lấy giá van gió).

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
data/<file>.xls(x)            (watcher bắt file mới / file bị ghi đè)
  1. XÁC ĐỊNH SẢN PHẨM (AI) : toàn bộ nội dung mọi sheet → OpenAI → header báo giá
             + danh sách sản phẩm (tên chuẩn, chủng loại, đơn vị, KL, kích thước
             đọc từ tên — chỉ là gợi ý)
  2. DATABASE : với MỌI sản phẩm (kể cả sản phẩm đã có trong báo giá vàng) lấy
             từ ChromaDB: dòng golden khớp nguyên văn, các dòng golden tương tự
             (đủ Mã SP, W1..E, diện tích, đơn giá, vật liệu, công thức), bảng mã
             SP kèm ví dụ, bộ công thức giá
  3. XÁC ĐỊNH KẾT QUẢ (AI) : sản phẩm + dữ liệu database → OpenAI → Mã SP (tương
             đối theo bảng mã), kích thước bảng tính diện tích theo cách golden đã
             điền cho sản phẩm cùng loại, công thức giá, vật liệu, xuất xứ, ghi chú
  4. KIỂM TRA + TÍNH : code loại giá trị không có căn cứ golden (log `drop …`),
             giá theo thứ tự: giá golden khớp nguyên văn → công thức golden →
             giá chép cùng quy cách; diện tích, thành tiền, tổng
  5. build : điền template (kèm bảng chú giải Mã SP) → reports/<file>.xlsx
```

Kiểm tra ở bước 4:
- Mã SP phải thuộc bảng mã và có căn cứ: khớp tên loại trong bảng mã, golden
  có gán mã đó cho tên bắt đầu giống vậy, hoặc dòng golden cùng sản phẩm mang
  mã đó; không có căn cứ → mã suy từ thống kê golden (`src/rag/masp.py`).
- Kích thước phải có trong tên/dòng gốc, hoặc lấy từ dòng golden khớp nguyên
  văn / cùng kích thước chính W1×H1 (quy ước như cút 300x300 → R=150). Sản phẩm
  khớp nguyên văn được bổ sung kích thước còn trống từ dòng golden đó.

- **Ưu tiên exact-match:** sản phẩm xuất hiện nguyên văn trong báo giá vàng
  dùng nguyên giá của nó (kể cả khi giá để trống).
- **Bộ công thức trong vector DB:** khi `build-index`, công thức ô đơn giá của
  từng dòng golden (.xlsx) được chuyển thành công thức theo trường (ô phụ cùng
  dòng thay bằng công thức/hằng số của nó), chỉ giữ khi tính lại ra đúng giá
  trị đã lưu (kết quả tương đương), chuẩn hóa để gộp biến thể trùng, rồi gom
  thành **tài liệu công thức** riêng (`kind=cong_thuc`, `src/rag/formulas.py`):
  cách tính đọc được theo tên cột Excel (VD `ROUND([m dài khung]*[đơn giá
  khung]+[m dài nan]*[đơn giá nan],0)` + định nghĩa), tham số giá, kích thước
  cần có, Mã SP + kích thước cố định dùng để đo diện tích (VD van MFD: `tb`,
  L=250), ví dụ sản phẩm thật và số lần dùng. Mỗi dòng golden trỏ tới công
  thức của nó (`cong_thuc_id`).
- **Chỉ theo golden data — AI chỉ chọn, không tạo:** bước XÁC ĐỊNH KẾT QUẢ truy xuất
  công thức cho từng hạng mục (công thức của sản phẩm tương tự + tìm trong bộ
  công thức) và gửi lên AI dạng "BỘ CÔNG THỨC F1, F2..."; AI trả `cong_thuc_tu`,
  hệ thống tự tính. Không có công thức thì hệ thống không tự lập công thức nào
  (không còn "diện tích × giá tôn × hệ số" tự suy, không quy ước diện tích ngoài
  công thức của template). Mọi giá trị AI trả (đơn giá, vật liệu, xuất xứ, ghi
  chú, Mã SP) được code kiểm tra phải trùng giá trị của một dòng golden; đơn
  giá chép còn phải cùng quy cách (cùng kích thước D150 ≠ 150x150, cùng mã
  hiệu). Giá trị không đạt bị loại (ghi log `drop ...`) → để trống cho người lập
  báo giá.
- **Report ghi công thức Excel** (đơn giá, thành tiền, diện tích/cái, tên ở
  bảng diện tích, dòng tổng/thuế) khi công thức cho kết quả bằng giá trị tính
  được; ngược lại ghi giá trị. File mở bằng Excel sẽ tự tính lại.
- **Không mượn giá khác chủng loại:** không có tri thức cùng loại thì đơn giá
  để trống cho người lập báo giá tự điền.
- **File lớn** được chia chunk (`CHUNK_ROWS` dòng/lần xác định sản phẩm,
  `RESULT_BATCH` sản phẩm/lần xác định kết quả, chạy song song `LLM_WORKERS`), hạng mục lặp giữa các sheet
  (sheet tổng hợp + sheet báo giá) được gộp.
- Bước READ lỗi → dùng parser heuristic làm dự phòng.
- Lần chạy lại chỉ bỏ qua file khi report mới hơn file input (`--force` để
  chạy lại bất kể).
- Truy vết từng dòng (tri thức đã dùng, khớp nguyên văn) nằm trong
  `rag_index/state.json`.

## Cấu trúc thư mục

```
data/              # BOQ đầu vào (watch)
report-template/   # mẫu báo giá chuẩn
golden-data/       # báo giá vàng — tri thức RAG
reports/           # output
rag_index/         # cache index (index.json) + state (state.json)
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
