# Tài liệu kiến trúc dự án

Hệ thống **RAG sinh báo giá Kaiyo Việt Nam** — tự động tạo báo giá xây lắp (file Excel)
từ các BOQ đầu vào, dùng RAG (học từ báo giá vàng) + LLM (OpenAI) để phân loại sản
phẩm và định giá.

## Danh mục tài liệu

| Tài liệu | Nội dung |
|---|---|
| [architecture.md](architecture.md) | Tổng quan kiến trúc, luồng dữ liệu, sơ đồ thành phần |
| [modules.md](modules.md) | Chi tiết từng module trong `src/` |
| [rag.md](rag.md) | Hệ thống RAG: golden-data, index ChromaDB, truy vấn |
| [pricing.md](pricing.md) | Công thức diện tích, trích kích thước, tính giá |
| [config.md](config.md) | Cấu hình, biến môi trường, lệnh CLI |

## Đọc nhanh

- Muốn hiểu **toàn cảnh hệ thống** → bắt đầu từ [architecture.md](architecture.md).
- Muốn sửa **logic parse / định giá** → xem [modules.md](modules.md) rồi [pricing.md](pricing.md).
- Muốn thêm **báo giá vàng mới** → đọc [rag.md](rag.md).
- Muốn **đổi cấu hình / đường dẫn** → xem [config.md](config.md).
