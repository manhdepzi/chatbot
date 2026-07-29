# Hệ thống báo giá HVAC AI

## 1. Tóm tắt báo cáo

Hệ thống này là ứng dụng Streamlit hỗ trợ sales/QS đọc file khối lượng HVAC, nhận diện sản phẩm, tính giá theo rule, học từ báo giá đã hoàn thành, cảnh báo các dòng chưa đủ chắc chắn và xuất báo giá Excel/PDF.

Mục tiêu vận hành không phải để LLM tự đoán giá. Giá phát hành phải dựa trên:

- Danh mục sản phẩm chuẩn.
- Alias/từ khóa đã duyệt.
- Công thức tính đã duyệt.
- Bảng giá chuẩn trên Supabase.
- Bộ nhớ báo giá đã hoàn thành.
- Checklist và phê duyệt của sales/QS.

Nếu hệ thống không chắc, nó phải cảnh báo và chặn xuất chính thức thay vì báo giá linh tinh.

## 2. Trạng thái hiện tại

- App chạy bằng Streamlit tại `app.py`.
- Supabase/PostgreSQL là database chính khi `USE_SUPABASE_DB=1`.
- SQLite chỉ còn dùng cho dev/test khi đặt `USE_SUPABASE_DB=0`.
- Đọc được Excel `.xlsx`, `.xls`; ảnh/PDF có thể đọc bằng Gemini OCR nếu cấu hình API key.
- Có màn import hàng loạt báo giá đã hoàn thành vào bộ nhớ AI.
- Có LLM enrichment cho các dòng parser không đọc/map được, nhưng dòng LLM tự suy luận vẫn bị kiểm soát bằng confidence.
- Có bộ nhớ báo giá, RAG nội bộ, quản trị alias/rule/bảng giá, phiên báo giá và phê duyệt.
- Có xuất Excel vào mẫu báo giá nếu nhận diện được template; nếu không nhận diện được thì xuất sheet dữ liệu kiểm tra.
- Hệ thống đang ưu tiên an toàn: dòng thiếu giá, thiếu size, không map được sản phẩm hoặc LLM không đủ tin cậy sẽ không được tự động phát hành.

## 3. Công nghệ sử dụng

Các thư viện chính trong `requirements.txt`:

- `streamlit`: giao diện web.
- `pandas`: đọc/xử lý bảng Excel.
- `openpyxl`: đọc/ghi `.xlsx`, giữ template Excel.
- `xlrd`: đọc file Excel cũ `.xls`.
- `psycopg[binary]`: kết nối PostgreSQL/Supabase.
- `requests`: gọi Supabase Storage/REST API.
- `google-generativeai`: gọi Gemini để OCR ảnh và xử lý dòng khó.
- `openai`: gọi OpenAI Responses API cho các dòng text khó nếu chọn `LLM_PROVIDER=openai`.
- `python-dotenv`: đọc biến môi trường trong `.env`.
- `pydantic`: validate/chuẩn hóa dữ liệu.
- `pytest`: chạy test tự động.

## 4. Cấu trúc thư mục

```text
app.py                         Giao diện Streamlit và luồng nghiệp vụ chính
backend/parser_engine.py        Đọc Excel/ảnh, nhận diện cột, bóc kích thước
backend/pricing_engine.py       Công thức tính giá, rule kỹ thuật, cảnh báo
backend/excel_engine.py         Xuất Excel vào mẫu báo giá
backend/intelligence.py         Readiness, giải thích, phiên, phê duyệt, PDF
backend/quote_memory.py         Học và tra bộ nhớ báo giá
backend/product_catalog.py      Danh mục sản phẩm chuẩn, alias, sales answer
backend/supabase_store.py       Kết nối Supabase/PostgreSQL/Storage
backend/llm_quote_enrichment.py LLM xử lý dòng khó khi import báo giá cũ
backend/rag_engine.py           So khớp báo giá cũ tương tự
supabase/schema.sql             Schema Supabase/PostgreSQL chính
supabase/README.md              Hướng dẫn riêng cho Supabase
supabase/check_config.py        Kiểm tra cấu hình Supabase
supabase/migrate_sqlite_to_supabase.py Migrate dữ liệu cũ từ SQLite
tests/                          Test parser, pricing, export, intelligence
```

## 5. Cấu hình môi trường

Tạo file `.env` từ `.env.example`.

Ví dụ cấu hình production:

```env
LLM_PROVIDER=gemini

GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash

OPENAI_API_KEY=your_openai_api_key_here
OPENAI_MODEL=gpt-4.1-mini

SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key_here
DATABASE_URL=postgresql://postgres:password@db.your-project.supabase.co:5432/postgres
USE_SUPABASE_DB=1
SUPABASE_DB_STRICT=1
SUPABASE_STORAGE_BUCKET=quotation-files
COMPANY_NAME=Kaiyo Việt Nam

ENABLE_EMBEDDINGS=0
EMBEDDING_MODEL=models/text-embedding-004
RAG_AUTO_APPLY_THRESHOLD=0.92
```

Lưu ý bảo mật:

- Không commit `.env` thật.
- Không đưa `SUPABASE_SERVICE_ROLE_KEY` lên frontend/public repo.
- `SUPABASE_URL` nên là domain gốc, ví dụ `https://xxx.supabase.co`, không cần thêm `/rest/v1`.
- Nếu mật khẩu database có ký tự đặc biệt, cần URL-encode trong `DATABASE_URL`.

Chọn provider LLM:

- `LLM_PROVIDER=gemini`: dùng Gemini cho LLM text và OCR ảnh.
- `LLM_PROVIDER=openai`: dùng OpenAI cho LLM text khi import báo giá/chuẩn hóa dòng khó.
- OCR ảnh hiện vẫn dùng Gemini OCR trong `parser_engine.py`; nếu muốn OCR ảnh bằng OpenAI cần bổ sung luồng vision riêng.

## 6. Cai dat va chay local

### 6.1. Yeu cau may chay

- Windows 10/11.
- Khuyen nghi Python 3.11 hoac 3.12.
- Co Internet de ket noi Supabase va goi AI API.
- Da co file `.env` cau hinh Supabase/API key.

Kiem tra Python:

```powershell
python --version
```

Neu may co nhieu ban Python, nen dung Python 3.11/3.12 de tao venv.

### 6.2. Cai moi tu dau

Vao dung thu muc du an. Vi du khi ban giao project nam tai:

```powershell
cd D:\Duan_Kaiyo\chatbot
```

Tao moi truong Python moi:

```powershell
python -m venv venv
```

Kich hoat moi truong:

```powershell
.\venv\Scripts\Activate.ps1
```

Cai thu vien:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Chay app:

```powershell
python -m streamlit run app.py
```

Mo trinh duyet:

```text
http://localhost:8501
```

Khuyen nghi luon chay bang:

```powershell
python -m streamlit run app.py
```

Khong nen chay bang:

```powershell
streamlit run app.py
```

Ly do: khi copy/move thu muc du an, file `venv\Scripts\streamlit.exe` co the van nho duong dan Python cu, gay loi launcher.

### 6.3. Chay production noi bo voi Supabase

Trong `.env` can co:

```env
USE_SUPABASE_DB=1
SUPABASE_DB_STRICT=1
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key_here
DATABASE_URL=postgresql://postgres:password@db.your-project.supabase.co:5432/postgres
SUPABASE_STORAGE_BUCKET=quotation-files
```

Voi cau hinh nay:

- App chi dung Supabase/PostgreSQL.
- SQLite runtime bi chan de tranh doc/ghi nham du lieu local.
- Neu Supabase loi, app bao loi that de ky thuat xu ly.

Chay:

```powershell
cd D:\Duan_Kaiyo\chatbot
.\venv\Scripts\Activate.ps1
python -m streamlit run app.py
```

### 6.4. Chay dev/test khong dung Supabase

Chi dung khi ky thuat muon test offline:

```powershell
cd D:\Duan_Kaiyo\chatbot
.\venv\Scripts\Activate.ps1
$env:USE_SUPABASE_DB="0"
python -m streamlit run app.py
```

Khong dung che do nay cho production.

### 6.5. Kiem tra cau hinh Supabase

Sau khi dien `.env`, chay:

```powershell
cd D:\Duan_Kaiyo\chatbot
.\venv\Scripts\Activate.ps1
python supabase\check_config.py
```

Neu bao thieu `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` thi can kiem tra lai `.env`.

### 6.6. Chay test ky thuat

```powershell
cd D:\Duan_Kaiyo\chatbot
.\venv\Scripts\Activate.ps1
$env:USE_SUPABASE_DB="0"
python -m pytest -q
```

Ket qua mong muon:

```text
19 passed
```

Neu SQLite local trong goi ban giao bi readonly, hay tao lai venv va chay test tren thu muc co quyen ghi, hoac dung Supabase production de van hanh app.

### 6.7. Loi thuong gap khi chay

#### Loi venv nho duong dan cu

Vi du loi:

```text
Fatal error in launcher: Unable to create process using
'"D:\chatbot\venv\Scripts\python.exe" "D:\Duan_Kaiyo\chatbot\venv\Scripts\streamlit.exe" run app.py'
The system cannot find the file specified.
```

Nguyen nhan:

- Project da bi copy/move tu `D:\chatbot` sang `D:\Duan_Kaiyo\chatbot`.
- `streamlit.exe` trong venv van nho duong dan Python cu.

Cach xu ly nhanh:

```powershell
cd D:\Duan_Kaiyo\chatbot
.\venv\Scripts\Activate.ps1
python -m streamlit run app.py
```

Neu van loi, tao lai venv:

```powershell
cd D:\Duan_Kaiyo\chatbot
Remove-Item -Recurse -Force .\venv
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m streamlit run app.py
```

#### Loi khong chay duoc script Activate.ps1

Neu PowerShell chan activate:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Sau do chay lai:

```powershell
.\venv\Scripts\Activate.ps1
```

#### Loi Supabase connection

Kiem tra:

```powershell
python supabase\check_config.py
```

Neu dung production, khong doi ve SQLite de ne loi. Can sua `DATABASE_URL`, network hoac Supabase project.

## 7. Khởi tạo Supabase

Các bước triển khai Supabase:

1. Tạo project Supabase.
2. Mở SQL Editor.
3. Chạy toàn bộ file `supabase/schema.sql`.
4. Tạo Storage bucket tên `quotation-files`.
5. Cấu hình `.env` với `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`.
6. Chạy kiểm tra cấu hình:

```powershell
.\venv\Scripts\python.exe supabase\check_config.py
```

7. Nếu có dữ liệu SQLite cũ cần migrate:

```powershell
.\venv\Scripts\python.exe supabase\migrate_sqlite_to_supabase.py
```

Khi app khởi động, `init_db()` sẽ seed dữ liệu nền vào Supabase:

- Company settings.
- Product categories.
- Product master.
- Product aliases.
- Materials.
- Thickness rules.
- Price list mặc định.
- Coefficients.
- Product pricing rules.

## 8. Quy trình báo giá chuẩn

1. Sales nhập `Khách hàng` và `Dự án`.
2. Upload file KL/BOQ khách gửi vào ô `File khối lượng khách gửi`.
3. Upload mẫu báo giá Excel vào ô `Mẫu báo giá Excel`.
4. Nếu là báo giá sửa đổi hoặc cần học giá tương tự, upload file báo giá đã hoàn thành vào mục `Tùy chọn: học/so sánh với báo giá đã làm`.
5. Chọn bảng giá.
6. Bấm `Phân tích khối lượng`.
7. Xem tab `Quy trình AI`, `Ước tính`, `Trợ lý sales/QS`, `Giải thích`, `So sánh`.
8. Xử lý tất cả cảnh báo/checklist.
9. QS hoặc người có quyền phê duyệt.
10. Xuất file nháp hoặc file chính thức.

File chính thức bị chặn nếu còn:

- Sản phẩm chưa xác định.
- Dòng thiếu giá.
- Dòng thiếu kích thước bắt buộc.
- Số lượng bất thường.
- Giá lấy từ RAG nhưng chưa đủ chắc.
- Bộ nhớ AI chỉ khớp một phần.
- Chưa có phê duyệt.

## 9. Import hàng loạt báo giá đã hoàn thành

Màn `Import hàng loạt báo giá đã hoàn thành` dùng để đưa nhiều file báo giá thật vào bộ nhớ AI.

Quy trình:

1. Chọn nhiều file `.xlsx` hoặc `.xls`.
2. Nhập dự án/khách hàng nguồn nếu cần.
3. Bật `Lưu bản file gốc lên Supabase Storage` để lưu truy vết.
4. Bật `Dùng AI/LLM xử lý dòng thiếu mã sản phẩm, kích thước hoặc công thức` nếu muốn LLM hỗ trợ các dòng parser không chắc.
5. Chọn `Giới hạn dòng gửi LLM mỗi file`.
6. Bấm `Import hàng loạt vào bộ nhớ AI`.

Ý nghĩa các cột kết quả:

- `dòng_đọc_được`: số dòng parser đọc ra từ file.
- `dòng_đã_học`: số dòng được lưu vào bộ nhớ AI đã duyệt.
- `dòng_có_thể_học`: số dòng đủ điều kiện học.
- `bỏ_qua_UNKNOWN`: dòng chưa map được sản phẩm chuẩn.
- `bỏ_qua_thiếu_giá`: dòng không có đơn giá/thành tiền hợp lệ.
- `bỏ_qua_LLM_thấp`: dòng LLM suy luận nhưng chưa đủ tin cậy.
- `dòng_gửi_LLM`: số dòng thực sự gửi lên model.
- `dòng_LLM_bổ_sung`: số dòng LLM bổ sung được thông tin.

Nếu `dòng_đã_học` thấp hơn `dòng_đọc_được`, đó là cơ chế an toàn. Mở bảng `Chi tiết các dòng chưa được học / cần sales-QS duyệt` để xem lý do từng dòng.

## 10. Giới hạn dòng gửi LLM mỗi file

Trường này giới hạn số dòng tối đa trong mỗi file được gửi lên Gemini/LLM.

Ví dụ đặt `200`:

- File có 50 dòng khó: gửi tối đa 50 dòng.
- File có 500 dòng khó: gửi tối đa 200 dòng.
- Import 8 file: tối đa khoảng `8 x 200 = 1600` dòng.

Chỉ các dòng parser chưa đủ chắc mới gửi LLM. Các dòng Excel đọc rõ không cần tốn token.

Khuyến nghị:

- Test ban đầu: `50-100`.
- Import thật nhưng muốn tiết kiệm: `150-200`.
- File nhiều biến thể khó: `300-500`, nhưng cần chú ý quota.

## 11. Token và chi phí LLM

Không hoặc gần như không tốn token khi:

- Parser đọc Excel bình thường.
- Lưu dữ liệu vào Supabase.
- So khớp alias/rule nội bộ.
- Tính giá bằng rule engine.
- Xuất Excel/PDF.

Có tốn token khi:

- OCR ảnh/PDF bằng Gemini.
- Bật LLM enrichment trong import hàng loạt.
- Chatbot gọi model thật để trả lời/phân tích.

LLM chỉ nên dùng để đọc hiểu/gợi ý cho dòng khó. Sau khi sales/QS xác nhận alias/rule mới, lần sau gặp lại hệ thống nên xử lý bằng database/rule, không cần gọi LLM nữa.

## 12. Cách AI học báo giá

AI không học theo kiểu tự đoán giá. Hệ thống chỉ lưu vào bộ nhớ các dòng đủ điều kiện:

- Có mô tả sản phẩm.
- Có số lượng hợp lệ.
- Có đơn giá hoặc thành tiền hợp lệ.
- Có category/mã sản phẩm đủ chắc.
- Nếu dùng LLM, confidence phải đạt ngưỡng an toàn.

Dữ liệu được lưu trong Supabase:

- `quote_memory_sources`: nguồn file báo giá đã học.
- `quote_memory_items`: từng dòng đơn giá đã học.
- `product_master`: mã sản phẩm chuẩn.
- `product_aliases`: từ khóa/biến thể map về mã chuẩn.
- `price_lists`, `price_list_items`: bảng giá chuẩn.
- `product_pricing_rules`: rule/công thức sản phẩm.
- `sales_product_answers`: câu trả lời/duyệt tay của sales/QS cho dòng chưa chắc.

## 13. Danh mục sản phẩm chuẩn cần quản trị

Các nhóm quan trọng:

- Ống gió vuông/chữ nhật.
- Cút/co vuông.
- Côn thu/côn giảm.
- Tê/chạc.
- Cửa gió Louver.
- Cửa gió kèm LCCT.
- Cửa/miệng gió kèm OBD.
- Van OBD.
- Van MFD L250.
- Van FD L250.
- Van một chiều.
- Ty ren/phụ kiện treo.
- Hộp gió, tiêu âm, cổ bạt, phụ kiện khác.

Với mỗi sản phẩm cần có:

- `product_code` chuẩn.
- Alias/từ khóa tiếng Việt, Anh, Trung.
- Required fields: W/H/D/L, độ dày, vật liệu, số lượng.
- Pricing method.
- Rule tính giá.
- Nguồn bảng giá.
- Điều kiện chặn nếu thiếu dữ liệu.

## 14. Vì sao đã học nhiều file nhưng vẫn không báo giá được

Các nguyên nhân thường gặp:

- File KL khách gửi có mô tả khác báo giá đã học nên signature không khớp.
- Dòng trong báo giá cũ được học là sản phẩm khác category.
- Thiếu alias cho biến thể từ khóa mới.
- Bảng giá mặc định vẫn là seed/demo, chưa phải đơn giá Kaiyo thật.
- File KL thiếu kích thước hoặc ghi kích thước ở cột khác.
- File upload nhầm: báo giá hoàn thành bị đưa vào ô `File khối lượng khách gửi`.
- Bộ nhớ AI chỉ khớp tương tự, chưa đủ ngưỡng `RAG_AUTO_APPLY_THRESHOLD`.
- Các dòng LLM suy luận bị confidence thấp nên bị chặn.

Đây là hành vi đúng cho business-safe: không chắc thì hỏi sales/QS, không tự báo giá.

## 15. Công thức/rule đã có trong code

Một số rule đã được thêm/đối chiếu:

- MFD L250:

```text
Diện tích/cái = 2 * (W + H) * L + W * H
L mặc định = 250 mm
Đơn giá/cái = diện tích * đơn giá tấm + tiền motor
```

- FD L250:

```text
Đơn giá/cái = diện tích * đơn giá tấm + phụ kiện FD
```

- Van một chiều bọc chống cháy:

```text
Đơn giá/cái = diện tích * đơn giá tấm + diện tích thân * phụ kiện chống cháy
```

- Cửa gió nan bầu dục + OBD:

```text
Đơn giá = ((chu vi * frame_rate) + (số nan * chiều rộng * blade_rate)) * hệ số
```

- Hệ số ống gió mã T:

```text
Ống nguyên: hệ số 1.0
Ống bù: hệ số 1.2
Còn lại: hệ số 1.3
```

Các rule này cần tiếp tục chuyển dần từ code sang bảng quản trị Supabase để admin sửa được mà không cần sửa mã nguồn.

## 16. Xuất Excel

App ưu tiên ghi vào mẫu báo giá đã upload.

Điều kiện để xuất giống form:

- Template phải là file `.xlsx`.
- Sheet báo giá phải nhận diện được, ví dụ `báo giá`, `Mẫu - xây lắp`, hoặc layout có header sản phẩm.
- Các cột cần ghi phải map được: STT, tên vật tư, vật liệu, xuất xứ, đơn vị, khối lượng, đơn giá, thành tiền, ghi chú.
- Nếu template không nhận diện được, app tạo sheet dữ liệu kiểm tra để tránh phá format.

File nháp dùng để kiểm tra. File chính thức chỉ mở khi trạng thái đủ điều kiện và đã phê duyệt.

## 17. Kiểm thử

Chạy kiểm tra cú pháp:

```powershell
$env:PYTHONIOENCODING="utf-8"
.\venv\Scripts\python.exe -m py_compile app.py backend\parser_engine.py backend\llm_quote_enrichment.py backend\supabase_store.py backend\quote_memory.py
```

Chạy test offline:

```powershell
$env:USE_SUPABASE_DB="0"
.\venv\Scripts\python.exe -m pytest
```

Kết quả kiểm thử gần nhất:

```text
16 passed
```

Cảnh báo hiện tại:

- `google.generativeai` đã bị Google đánh dấu deprecated. App vẫn chạy, nhưng về sau nên chuyển sang SDK `google.genai`.

## 18. Triển khai production

Checklist triển khai:

1. Tạo Supabase project.
2. Chạy `supabase/schema.sql`.
3. Tạo Storage bucket `quotation-files`.
4. Cấu hình `.env` thật.
5. Chạy `supabase/check_config.py`.
6. Chạy app local kiểm tra seed dữ liệu.
7. Import bộ báo giá vàng đã hoàn thành.
8. Kiểm tra bảng chi tiết dòng chưa học.
9. Bổ sung alias/rule/bảng giá cho các nhóm thiếu.
10. Chạy test bằng các file KL thực tế.
11. Chỉ bật xuất chính thức khi checklist pass và có phê duyệt.

Khi deploy server:

- Không dùng SQLite production.
- Không commit secrets.
- Bật log lỗi parser/LLM/Supabase.
- Backup Supabase định kỳ.
- Phân quyền admin/sales/QS/approver rõ ràng.

## 19. Quyền quản trị và phê duyệt

Vai trò khuyến nghị:

- Admin: quản lý bảng giá, alias, rule, người dùng, settings.
- QS/Estimator: kiểm tra công thức, duyệt dòng chưa chắc, xác nhận rule.
- Sales: upload file, nhập thông tin khách hàng, xử lý checklist thương mại.
- Approver: phê duyệt báo giá trước khi phát hành.
- Viewer: chỉ xem.

Luồng duyệt:

1. AI/parser tạo báo giá nháp.
2. Sales/QS xử lý checklist.
3. QS xác nhận các dòng thiếu alias/rule/giá.
4. Người có quyền approver phê duyệt.
5. App mở nút xuất chính thức.

## 20. Giới hạn và rủi ro

Không nên cam kết đúng 100% nếu:

- Chưa có bảng giá chuẩn đầy đủ.
- Chưa có rule cho từng product_code.
- Chưa có alias cho biến thể mô tả của khách hàng.
- File khách gửi là scan/ảnh mờ.
- Template báo giá mới chưa được map.
- Bộ nhớ AI chứa dữ liệu test/debug hoặc báo giá chưa duyệt.

Để tiến tới độ chính xác rất cao, cần xây bộ dữ liệu vàng:

- 100-500 file báo giá đã hoàn thành, sạch, đã duyệt.
- Mỗi dòng có product_code chuẩn.
- Bảng alias đa ngôn ngữ.
- Bảng rule tính giá có version.
- Bảng giá theo vật liệu, độ dày, nhà cung cấp, khu vực, thời điểm.
- Test tự động so sánh tổng tiền với file gốc.

## 21. Roadmap đề xuất

Ưu tiên tiếp theo:

1. Chuẩn hóa toàn bộ product_code và alias từ các file báo giá đã học.
2. Tạo màn hình duyệt dòng import chưa học: chấp nhận alias, sửa product_code, nhập giá/rule.
3. Tách toàn bộ rule đặc thù MFD/FD/OBD/LCCT/ống gió sang Supabase.
4. Tạo bộ test vàng cho từng loại báo giá.
5. Thêm dashboard độ chính xác: số dòng khớp, số dòng hỏi sales, sai lệch tổng tiền.
6. Chuyển Gemini SDK từ `google-generativeai` sang `google.genai`.
7. Hoàn thiện mapping template để luôn xuất đúng form Kaiyo khi có mẫu chuẩn.

## 22. Kết luận

Hệ thống hiện đã có nền tảng để vận hành báo giá HVAC AI theo hướng an toàn: đọc file, học báo giá cũ, quản lý Supabase, cảnh báo thiếu dữ liệu, phê duyệt và xuất file.

Tuy nhiên để thay thế nhân viên báo giá trong doanh nghiệp, phần quan trọng nhất không phải là cho LLM đoán nhiều hơn, mà là chuẩn hóa dữ liệu:

- Product master.
- Alias.
- Rule.
- Bảng giá.
- Bộ báo giá vàng.
- Quy trình duyệt.

Khi các lớp này đủ sạch, AI sẽ hoạt động như trợ lý báo giá ổn định: dòng nào chắc thì tự tính, dòng nào không chắc thì hỏi sales/QS, và chỉ xuất chính thức khi đủ điều kiện.
