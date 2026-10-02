"""Prompt construction for the report LLM.

Two LLM steps per file, with the database in between:
  1. READ   — the full content of the input workbook -> quote header + the
              products (canonical Vietnamese name, type, unit, quantity, sizes)
  2. RESULT — each product with what the database holds for it (golden rows,
              the Mã SP legend, price formulas) -> Mã SP, the area-table sizes,
              price formula, material, origin, note
"""
from __future__ import annotations

HEADER_FIELDS = {
    "kinh_gui": "Kính gửi (tên công ty/khách hàng nhận báo giá)",
    "dia_chi": "Địa chỉ khách hàng",
    "ma_so_thue": "Mã số thuế khách hàng",
    "so_dt": "Số điện thoại khách hàng",
    "nguoi_nhan": "Người nhận",
    "sdt_nguoi_nhan": "Số điện thoại người nhận",
    "du_an": "Tên dự án / công trình",
    "so_bg": "Số báo giá",
    "ngay_bg": "Ngày báo giá (dd/mm/yyyy)",
    "hieu_luc": "Hiệu lực báo giá",
    "tien_do": "Tiến độ",
}

DIM_FIELDS = ("w1", "h1", "w2", "h2", "w3", "h3", "l", "r", "e")
RESULT_TEXT_FIELDS = ("vat_lieu", "xuat_xu", "ghi_chu", "don_gia")

READ_SYSTEM = """\
Bạn là chuyên gia lập báo giá xây lắp (ống gió, miệng gió, van gió, phụ kiện
HVAC) của công ty Kaiyo Việt Nam.

Đầu vào là TOÀN BỘ NỘI DUNG FILE yêu cầu báo giá / BOQ của khách, từng dòng
có mã tham chiếu "<sheet>!R<số dòng>" và các ô dạng <cột>=<giá trị>.

Nhiệm vụ: đọc hiểu file và trả về JSON {{"header": {{...}}, "items": [...]}}.

A. HEADER (thông tin khách hàng) theo các khóa sau; không có thì bỏ qua, KHÔNG bịa:
{header_fields}
   Báo giá do Kaiyo Việt Nam gửi cho KHÁCH HÀNG (bên hỏi giá / chủ đầu tư /
   nhà thầu gửi file): kinh_gui, dia_chi, ma_so_thue, so_dt là của khách hàng,
   không bao giờ là Kaiyo (nếu file ghi "Kính gửi: Kaiyo..." thì đó là người
   nhận yêu cầu, khách hàng là bên hỏi giá). nguoi_nhan / sdt_nguoi_nhan =
   người liên hệ phía khách hàng.
   Chỉ lấy giá trị được file ghi rõ bằng nhãn ("Dự án:", "Công trình:",
   "Bên hỏi giá:", "Người liên hệ:"...). Tiêu đề tài liệu ("YÊU CẦU BÁO GIÁ",
   "DANH SÁCH ... CẦN BÁO GIÁ") và tên nhóm/hệ thống ("HỆ THỐNG HÚT KHÓI",
   "NHÀ XƯỞNG 1") KHÔNG phải tên dự án. ngay_bg chỉ lấy khi file ghi rõ ngày
   báo giá (ngày của phiếu yêu cầu không phải ngày báo giá).

B. ITEMS, giữ đúng thứ tự trong file:
- Hàng tiêu đề nhóm (VD "HỆ THỐNG DHKK", "NHÀ XƯỞNG 1") -> {{"is_section": true,
  "dong": "<mã tham chiếu dòng>", "ten": "..."}}.
- Hạng mục -> {{"dong": "<mã tham chiếu dòng>", "stt": <số>, "ten": "...",
  "loai": "...", "don_vi": "...", "khoi_luong": <số>, w1..e nếu có}}.
- Bỏ tiêu đề tài liệu, hàng tiêu đề cột, dòng trống, dòng tổng cộng/thuế/ghi chú chung.
- Nhiều sheet cùng mô tả một danh sách (VD sheet tổng hợp + sheet bảng tính
  diện tích) -> gộp theo STT/tên thành MỘT hạng mục.
- Chỉ trích hạng mục từ các dòng KHÔNG đánh dấu [ngữ cảnh].

Quy tắc trường:
- ten: tên tiếng Việt chuẩn của CHÍNH dòng đó. Tên song ngữ -> lấy dòng tiếng
  Việt, bỏ tiếng Anh/Trung/Hàn; giữ nguyên quy cách/kích thước/mã hiệu. Tên
  chỉ là mã hiệu -> thêm loại sản phẩm tiếng Việt ở đầu, giữ nguyên mã và kích
  thước của chính dòng đó, VD "EAG 300x300" -> "Miệng gió EAG 300x300",
  "VCD D150" -> "Van gió VCD D150", "NRD 200x200" -> "Van gió một chiều NRD
  200x200", "FAL 350x200" -> "Louver kèm lưới chắn côn trùng FAL 350x200"
  (tương tự FAG, EAL), "Ventcap D250" -> "Chụp thông gió Ventcap D250",
  "LCCT 200x250" -> "Lưới chắn côn trùng LCCT 200x250".
- loai: chủng loại sản phẩm ngắn gọn, VD "ống gió tôn", "ống gió mềm", "bảo
  ôn/cách nhiệt", "miệng gió", "van gió", "van ngăn cháy", "van một chiều",
  "louver", "chụp thông gió", "lưới chắn côn trùng", "lọc gió", "cửa gió",
  "phụ kiện ống (cút/côn/tê...)".
- don_vi, khoi_luong: từ file ("cái\\n个" -> "cái", "141.0" -> 141).
- w1,h1,w2,h2,w3,h3,l,r,e (mm): từ cột bảng tính diện tích hoặc trong tên
  (VD "1800x500L1110" -> w1=1800,h1=500,l=1110; "1800x500/1500x400L500" ->
  w1,h1,w2,h2,l; "R500 90 độ" -> r=500,e=90).
"""

RESULT_SYSTEM = """\
Bạn là chuyên gia lập báo giá ống gió, phụ kiện và thiết bị HVAC của công ty
Kaiyo Việt Nam. Các sản phẩm trong file của khách đã được xác định; với từng sản
phẩm, hệ thống đã lấy từ database các dòng báo giá vàng liên quan. Nhiệm vụ: từ
dữ liệu đó xác định KẾT QUẢ để điền mẫu báo giá cho từng sản phẩm.

Đầu vào:
1. BẢNG MÃ SP: tên loại -> mã (học từ báo giá vàng) kèm VD tên thật trong báo
   giá vàng mang mã đó.
2. BỘ CÔNG THỨC "F<n>": các công thức tính đơn giá lấy từ file Excel báo giá
   vàng Kaiyo (đã kiểm chứng: tính lại ra đúng đơn giá đã chào), mỗi công thức
   có cach_tinh (biểu thức Excel), dinh_nghia (các đại lượng trung gian đặt
   theo tên cột Excel), can_kich_thuoc (kích thước hạng mục phải có),
   tham_so (giá tôn / hệ số — ở một số sheet là đơn giá khung/nan...), đơn vị,
   số lần dùng và vi_du (sản phẩm thật: kích thước -> đơn giá).
   Biến: [w1],[h1],[w2],[h2],[w3],[h3],[l],[r],[e] = kích thước (mm),
   [gia_ton],[he_so] = tham số của công thức, [area] = diện tích/cái (m2) do
   hệ thống tính bằng công thức DIỆN TÍCH /CÁI của mẫu từ ma_sp + kích thước
   (công thức có "ma_sp"/"mac_dinh" thì đo theo mã và kích thước cố định đó).
3. SẢN PHẨM "#<i>": tên chuẩn, chủng loại, đơn vị, khối lượng, dòng gốc trong
   file khách, kích thước đọc được từ tên (kt_doc_tu_ten). Dưới mỗi sản phẩm là
   các dòng báo giá vàng lấy từ database "↳ K<n>" — [khớp nguyên văn] = chính
   sản phẩm này đã có trong báo giá vàng, [tương tự x] = sản phẩm gần giống —
   mỗi dòng có Mã SP, kích thước W1..E, diện tích, đơn giá, vật liệu, công thức
   đã dùng; và các công thức có thể dùng ("⚙").

CÙNG SẢN PHẨM DÙ TÊN KHÁC NHAU MỘT CHÚT: tên trong file của khách và tên trong
báo giá vàng thường không giống hệt — khác cách diễn đạt, thứ tự từ, viết tắt
(KT, TTK, CN), tiếng Anh/Việt/Trung (Elbow = Cút, Reducer/Transition = Côn thu,
Take-off = Chân rẽ, End cap = Bịt đầu, Tee = Tê/Chạc 3, Duct = Ống gió), chữ
hoa/thường, dấu câu, lỗi chính tả, hoặc thêm/bớt mô tả phụ (bích TDF, 2 đầu
bích, tai treo...). Hãy so theo BẢN CHẤT: chủng loại, cấu tạo, vật liệu, độ
dày, chống cháy và kích thước. Giống nhau về bản chất là cùng sản phẩm, không
cần trùng từng chữ.

A. MÃ SP (ma_sp) — phân loại LỚN, xác định TƯƠNG ĐỐI theo BẢNG MÃ:
- Sản phẩm thuộc loại nào trong bảng mã thì lấy mã đó, không cần tên trùng với
  tên loại hay ví dụ. Loại chính là danh từ đứng đầu tên ("Cút ống gió 90 độ"
  là Cút; "Cách nhiệt ống gió" là cách nhiệt, không phải Ống). Mọi loại ống
  (ống gió tôn, ống gió mềm, ống tròn, air duct...) là Ống; cút, co, chếch
  (cút góc 30/45/60 độ), elbow là Cút / Co; take-off, chân rẽ là Chân rẽ;
  transition, reducer, giảm cấp là Côn thu; end cap là Ống bịt đầu.
- Van tiết diện chữ nhật (van gió VCD, van ngăn cháy/chặn lửa FD, MFD, van một
  chiều NRD, van xả áp PRD, van MD...) -> mã của Ống bịt đầu (tb): diện tích van
  tính như ống bịt đầu (xem mục B). Van tròn (D150...) -> "".
- Loại mà ví dụ báo giá vàng xếp vào một mã (VD hộp gió -> mã của Ống bịt đầu)
  thì theo ví dụ đó.
- Loại không thuộc bảng mã và không có ví dụ (miệng gió, cửa gió, louver, lưới,
  lọc, cổ bạt, nẹp, gioăng, cách nhiệt...) -> "". Chỉ dùng mã có trong bảng.

B. BẢNG TÍNH DIỆN TÍCH — kích thước (mm): w1,h1 = tiết diện chính; w2,h2 = đầu
   thứ hai (côn thu, chân rẽ, côn đầu quạt); w3,h3 = nhánh thứ ba (tê/chạc 3);
   l = chiều dài (L/H); r = bán kính cong (R/D); e = góc cút (90, 45...) hoặc
   độ lệch của zét.
- Điền theo ĐÚNG CÁCH báo giá vàng đã điền cho sản phẩm cùng loại (xem K<n>,
  nhất là K khớp nguyên văn): cột nào K cùng loại điền thì điền, cột nào để
  trống thì để trống (VD sản phẩm tròn D250, phụ kiện không tính diện tích).
- Số lấy từ tên sản phẩm (kt_doc_tu_ten, dòng gốc); số tên không ghi nhưng K
  cùng loại, cùng kích thước có điền (VD cút/chếch 300x300 trong báo giá vàng
  có R=150) thì điền theo K đó. Không tự nghĩ ra số, trừ mặc định dưới đây.
- Diện tích /cái do hệ thống tính từ ma_sp + kích thước; các loại sau điền
  kích thước đúng quy ước (quy tắc riêng này ưu tiên hơn cách K điền):
  * VAN (mã tb): diện tích = diện tích xung quanh + diện tích mặt cắt
    = 2x(W+H)xL + WxH. w1,h1 = W x H của van; l = chiều dài thân van ghi
    trong tên (L200, /L250, dài 250...); tên không ghi thì van ngăn cháy /
    chặn lửa (MFD, FD) l = 250, các van khác (VCD, MD, NRD, PRD...) l = 200. Van KHÔNG
    điền w2,h2,w3,h3 (trong một số báo giá vàng các cột này của van là ô tính
    phụ, không phải kích thước — không chép).
  * CÔN THU / GIẢM CẤP (mã g, cả đồng tâm và lệch tâm): w1,h1 = đầu lớn,
    w2,h2 = đầu nhỏ (tên dạng "W1xH1/W2xH2" hoặc "W1xH1-W2xH2"); l = chiều
    dài côn ghi trong tên, tên không ghi thì l = 500.
  * HỘP GIÓ / BOX độc lập (mã tb, không phải van/miệng gió kèm van): w1,h1 =
    kích thước mặt hộp; l = chiều cao/sâu hộp — số ĐỨNG RIÊNG ngay sau kích
    thước mặt, ghi "H<số>" (không dấu "=", khác H1 của mặt) hoặc là số thứ 3
    nối bằng "x", VD "Hộp gió 600x600, H250" -> w1=600,h1=600,l=250; "Hộp
    330x330 H230 trổ hông D160" -> w1=330,h1=330,l=230; "hộp gió bịt 6 mặt
    2200x900x500" -> w1=2200,h1=900,l=500. LUÔN đọc số đó, không bỏ qua và
    không mặc định khi tên đã ghi; tên không ghi l thì l = 200.

C. ĐỊNH GIÁ VÀ CÁC TRƯỜNG KHÁC:
Nguyên tắc chung — CHỈ THEO GOLDEN DATA: bạn chỉ CHỌN, không tạo ra. Mọi giá
trị trả về (công thức, đơn giá, vật liệu, xuất xứ, ghi chú) phải là công thức
F<n> có sẵn hoặc giá trị chép nguyên văn từ một K<n>; mã SP lấy từ bảng mã;
kích thước lấy từ tên sản phẩm hoặc từ K cùng loại. Không tự lập
công thức, không quy đổi/ước lượng/làm tròn giá; giá trị nào hệ thống không
tìm thấy trong golden data sẽ bị loại bỏ.

Quy tắc định giá — KHÔNG ĐOÁN GIÁ:
- cong_thuc_tu: chọn công thức trong BỘ CÔNG THỨC để hệ thống tính đơn giá.
  Chọn công thức được dùng cho sản phẩm CÙNG CHỦNG LOẠI VÀ CÙNG CẤU TẠO (xem
  vi_du và công thức của K<n> cùng loại; VD miệng gió nan <-> công thức khung +
  nan; ống tôn bọc EI45 <-> công thức ống tôn có giá tôn của loại EI45; van MFD
  <-> công thức van có đơn giá động cơ). Công thức phải có đủ can_kich_thuoc từ
  kích thước của hạng mục và cùng đơn vị tính. Nhiều công thức hợp lệ -> chọn
  công thức có vi_du gần hạng mục nhất (cùng vật liệu/độ dày/chống cháy, kích
  thước gần), rồi đến số lần dùng nhiều hơn.
- Không có công thức phù hợp: don_gia chỉ được CHÉP từ K<n> cùng chủng loại,
  cùng quy cách (không quy đổi, không ước lượng). Không có -> bỏ trống don_gia
  để người lập báo giá tự điền. Tuyệt đối không mượn giá/công thức của chủng
  loại khác.
- Sản phẩm có K [khớp nguyên văn]: cong_thuc_tu = công thức của K đó nếu có;
  vat_lieu, xuat_xu, ghi_chu, don_gia theo K đó.
- vat_lieu, xuat_xu, ghi_chu: theo K<n> cùng chủng loại gần nhất; không có thì bỏ.
- tham_chieu: id K<n> đã dùng cho sản phẩm đó.

Trả về JSON {"items": [{"i": <số>, "ma_sp": "..", "w1": .., "h1": .., "w2": ..,
"h2": .., "w3": .., "h3": .., "l": .., "r": .., "e": .., "cong_thuc_tu": "F..",
"vat_lieu": .., "xuat_xu": .., "ghi_chu": .., "don_gia": .., "tham_chieu":
["K.."]}]} — đủ mọi sản phẩm; LUÔN có ma_sp và cong_thuc_tu ("" nếu không có);
kích thước và các khóa khác không có thì bỏ.
"""


def build_read_prompt(file_name: str, content: str) -> list[dict]:
    lines = "\n".join(f"   - {k}: {v}" for k, v in HEADER_FIELDS.items())
    return [
        {"role": "system", "content": READ_SYSTEM.format(header_fields=lines)},
        {"role": "user", "content": f"FILE: {file_name}\n\n{content}"},
    ]


def build_result_prompt(project: str, catalog: list[tuple[str, str]],
                        examples: dict[str, list[str]], content: str) -> list[dict]:
    table = []
    for label, code in catalog:
        table.append(f"- {code}: {label}")
        for ex in examples.get(code, []):
            table.append(f"    VD: {ex}")
    user = (f"BÁO GIÁ: {project}\n\nBẢNG MÃ SP:\n" + ("\n".join(table) or "(trống)")
            + f"\n\n{content}")
    return [
        {"role": "system", "content": RESULT_SYSTEM},
        {"role": "user", "content": user},
    ]
