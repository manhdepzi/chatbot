# Supabase database design

Có thể làm trên Supabase. Supabase dùng PostgreSQL nên phù hợp hơn SQLite cho bản công ty: nhiều người dùng, phân quyền, backup, file storage, audit log và duyệt dữ liệu trước khi AI học.

## Cách dùng

1. Tạo project Supabase.
2. Mở **SQL Editor**.
3. Chạy toàn bộ file `supabase/schema.sql`.
4. Tạo bucket Storage tên `quotation-files`.
5. Thêm biến môi trường vào `.env`.

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
DATABASE_URL=postgresql://postgres:password@db.your-project.supabase.co:5432/postgres
USE_SUPABASE_DB=1
```

Không đưa `SUPABASE_SERVICE_ROLE_KEY` lên frontend hoặc public repo.

Để tạm quay về SQLite khi debug, đặt:

```env
USE_SUPABASE_DB=0
```

## Luồng dữ liệu đề xuất

1. Sales tải file khối lượng khách gửi lên `project_files`.
2. Sales tải mẫu báo giá lên `project_files`.
3. Nếu có báo giá đã hoàn thành, lưu vào `quote_memory_sources` với trạng thái `pending`.
4. Admin/manager duyệt source.
5. Backend mới đưa từng dòng giá vào `quote_memory_items` trạng thái `approved`.
6. Khi báo giá mới, AI chỉ tự áp giá từ `quote_memory_items.approval_status = 'approved'`.
7. Dòng nào không có giá duyệt thì báo `needs_input`, không cho xuất chính thức.
8. Khi xuất báo giá, lưu session vào `quotation_sessions`, từng dòng vào `quotation_items`, file Excel vào Storage.
9. Người duyệt ký ở `quotation_approvals`.

## Bảng quan trọng

- `companies`, `company_members`, `profiles`: công ty, người dùng, phân quyền.
- `project_files`: file KL, mẫu, báo giá cũ, báo giá xuất.
- `price_lists`, `price_list_items`: bảng giá theo NCC/khu vực/khách hàng/thời điểm.
- `product_pricing_rules`, `thickness_rules`, `coefficient_rules`: quy tắc tính.
- `unit_conversions`: quy đổi như `1 ống = 8 m`, làm tròn lên nguyên ống.
- `quote_memory_sources`, `quote_memory_items`: bộ nhớ AI từ báo giá đã duyệt.
- `quotation_sessions`, `quotation_items`: lịch sử phiên báo giá.
- `quotation_approvals`: duyệt trước khi xuất chính thức.
- `audit_events`: ai sửa gì, trước/sau ra sao.

## Vì sao thiết kế này an toàn hơn

- AI không học trực tiếp từ file chưa duyệt.
- Mỗi giá đều biết nguồn: bảng giá, báo giá cũ, nhập tay, rule hay AI gợi ý.
- Có thể truy lại ai sửa giá và lúc nào.
- Có thể dùng nhiều sales cùng lúc.
- Có thể backup/restore bằng Supabase/PostgreSQL.

## Bước triển khai tiếp theo

1. Cài dependency mới: `pip install -r requirements.txt`.
2. Kiểm tra cấu hình: `python supabase/check_config.py`.
3. Migrate dữ liệu cũ từ `backend/hvac_quotation.db`: `python supabase/migrate_sqlite_to_supabase.py`.
4. App hiện đã đọc/ghi Supabase cho bảng giá, quy tắc, bộ nhớ báo giá, phiên báo giá và phê duyệt. Nếu Supabase lỗi, app fallback SQLite.
5. File upload và file xuất đã được lưu lên Supabase Storage bucket `quotation-files`, đồng thời ghi metadata vào `project_files`.
6. Thêm màn hình đăng nhập và phân quyền.
7. Bắt buộc quy trình duyệt trước khi `quote_memory_items` được dùng để báo giá tự động.

## Phần đã nối app sang Supabase

- `price_lists`, `price_list_items`
- `coefficient_rules`
- `product_pricing_rules`
- `quote_memory_sources`, `quote_memory_items`
- `quotation_sessions`, `quotation_items`
- `quotation_approvals`
- Supabase Storage `quotation-files` và bảng metadata `project_files`

Các phần còn đang dùng SQLite tạm thời:

- Một số setting nhỏ trong `company_settings`
- Các bảng admin editor dạng raw table trong Streamlit
- File tạm local chỉ còn dùng trong lúc parser đọc nội dung upload, sau đó file vẫn được lưu lâu dài trên Storage
