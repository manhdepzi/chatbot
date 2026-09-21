# Cơ chế định giá

Module `src/pricing/calculator.py` chứa toàn bộ **số học** (không qua LLM), đảm bảo
đúng chính xác. Luồng định giá mỗi item do `pipeline._price_item()` điều phối.

## Luồng định giá một item (`_price_item`)

1. Nếu item là **section** (`is_section=True`) → trả nguyên, không định giá.
2. **Bù kích thước** — `_fill_dims_from_name`: nếu các trường `w1/h1/w2/…/l/r/e`
   rỗng, trích từ tên sản phẩm bằng `calc.extract_dims_from_name()`.
3. **Exact-match** — `retriever.exact_match()`:
   - khớp nguyên văn → chép các trường `ma_sp`, `vat_lieu`, `xuat_xu`, `don_vi`,
     `gia_ton`, `he_so`, `ty_ren`, `met_dai_ty`, `ghi_chu`, `don_gia` từ meta vàng;
   - `don_gia` lấy nguyên từ nguồn (**có thể là None** nếu nguồn để trống);
   - tính `area` + `thanh_tien`, **skip LLM hoàn toàn**.
4. **Semantic** — `retriever.retrieve(top_k)` → `llm.chat_json()`:
   - dùng `build_item_prompt(item, examples)` để hỏi LLM;
   - kết quả LLM **không ghi đè** giá trị đã có (chỉ điền chỗ trống);
   - nếu LLM không trả `don_gia` → lấy từ ví dụ khớp nhất (nếu có giá).
5. **Fallback đơn giá** — nếu vẫn không có `don_gia` nhưng có `area` + `gia_ton`:
   `don_gia = area * gia_ton * (he_so or 1.0)`.
6. **Thành tiền** — `thanh_tien = don_gia * khoi_luong` (giá rỗng → ghi `0`).

## Trích kích thước từ tên (`extract_dims_from_name`)

Regex khôi phục hình học từ chính tên sản phẩm Kaiyo:

| Mẫu tên | Kết quả |
|---|---|
| `Ống tôn mạ kẽm 1800x500L1110mm` | `w1=1800 h1=500 l=1110` |
| `Cút 90 độ ... KT 1800x500 R500` | `w1=1800 h1=500 r=500 e=90` |
| `Côn thu ... KT 1800x500/1500x400L500` | `w1 h1 w2 h2 l` |
| `Cửa bù khí nan chữ Z 1100x2000 ...` | `w1=1100 h1=2000` |
| `600X200, H=300` | `w1=600 h1=200 e=300` |

Quy tắc:
- **Reducer** (có `/` giữa hai nhóm `WxH`) → `w1/h1/w2/h2`.
- **Một nhóm `WxH`** → chỉ `w1/h1` (dù tên lặp lại).
- `L<NN>` → `l` (dài), `R<NN>` → `r` (bán kính), `NN độ` → `e` (góc), `H=NN` → `e`
  (độ rơi của zét down-up).

## Công thức diện tích (`compute_area`)

Branch theo `ma_sp` (chép nguyên từ cột `DIỆN TÍCH /CÁI` của báo giá vàng):

| Mã SP | Hình dạng | Công thức |
|---|---|---|
| `t` / `TA` | Ống | `2*(W1+H1)*L / 1e6` |
| `F` / `M` | Miệng gió | `W1*H1 / 1e6` |
| `d` | Zét down-up | `(W1+H1+E/2)*2*L / 1e6` |
| `tb` | Đầu bịt ống | `((W1+H1)*2*L + W1*H1) / 1e6` |
| `g` / `n` | Côn thu / chân rẽ | trapezoid của `(W1,W2,H1,H2,L)` |
| `c*` (cv) | Cút/co | cung tròn của `(W1,H1,R,E)` |
| `vt` | Côn đầu quạt | reducer + vành quạt của `(R)` |
| `tt` | Tê/chạc 3 | hai cung nhánh |

Nếu không có `ma_sp` nhưng có `W1,H1` (không `L`, không `R`) → coi như tấm phẳng:
`W1*H1 / 1e6`.

Nếu input đã cho sẵn `area` → dùng giá trị input, không tính lại.

## Tổng (`totals`)

```
truoc_thue = Σ thanh_tien
vat10      = truoc_thue * 0.10
vat8       = 0.0          # fallback giữ nguyên, không dùng
sau_thue   = truoc_thue + vat10
```

## Phân loại mã SP (LLM)

`llm/prompts.py` quy định bảng mã SP cho LLM:

| Mã | Ý nghĩa |
|---|---|
| `t` | Ống |
| `tb` | Đầu bịt ống |
| `cv` | Cút/co |
| `g` | Côn thu / giảm cấp |
| `d` | Zét / down-up |
| `n` | Chân rẽ / nối chân |
| `vt` | Côn đầu quạt |
| `tt` | Tê / chạc 3 |

Nếu sản phẩm là miệng gió / cửa / van (không phải ống tôn), `ma_sp` = chính tên
sản phẩm.
