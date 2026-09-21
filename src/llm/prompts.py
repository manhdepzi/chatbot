"""Prompt construction for the pricing LLM."""
from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """\
Bạn là chuyên gia định giá báo giá xây lắp của công ty Kaiyo Việt Nam.
Nhiệm vụ: cho một dòng sản phẩm (vật tư) cùng các ví dụ tương tự đã có giá (từ
báo giá cũ), hãy xác định các trường chuẩn cho dòng đó và ước tính đơn giá.

QUY TẮC:
- Mã SP (ma_sp): phân loại 1 trong: t (ống), tb (đầu bịt ống), cv (cút/co),
  g (côn thu/giảm cấp), d (zét/down-up), n (chân rẽ/nối chân), vt (côn đầu quạt),
  tt (tê/chạc 3). Nếu sản phẩm là miệng gió/cửa/van (không phải ống tôn) thì
  ma_sp = tên sản phẩm (chép lại tên).
- vật liệu chế tạo (vat_lieu), xuất xứ (xuat_xu): học từ ví dụ tương tự.
- đơn vị (don_vi): giữ nguyên đơn vị từ input nếu có.
- giá tôn (gia_ton), hệ số (he_so), ty ren (ty_ren): nếu là ống tôn mạ kẽm bọc
  chống cháy thì ưu tiên chép từ ví dụ khớp nhất; gia_ton mặc định 330000.
- đơn giá (don_gia): nếu ví dụ có dòng khớp chính xác tên/khối lượng thì chép
  đơn giá đó; ngược lại ước tính theo quy tắc giá của ví dụ. Nếu không đủ thông
  tin thì để don_gia = null.
- Chỉ trả về JSON hợp lệ, không giải thích gì thêm.
"""


def _example(doc: dict) -> dict:
    meta = doc.get("meta", {})
    return {
        "ten": doc.get("text", ""),
        "ma_sp": meta.get("ma_sp"),
        "vat_lieu": meta.get("vat_lieu"),
        "xuat_xu": meta.get("xuat_xu"),
        "don_vi": meta.get("don_vi"),
        "don_gia": meta.get("don_gia"),
        "gia_ton": meta.get("gia_ton"),
        "he_so": meta.get("he_so"),
        "ty_ren": meta.get("ty_ren"),
        "ghi_chu": meta.get("ghi_chu"),
    }


def build_item_prompt(item: dict[str, Any], examples: list[dict]) -> list[dict]:
    ex = [{"score": d.get("score"), **_example(d)} for d in examples]

    user = (
        "Dòng sản phẩm cần định giá:\n" + json.dumps(item, ensure_ascii=False) +
        "\n\nCác ví dụ tương tự đã có giá (sắp theo độ tương đồng):\n" +
        json.dumps(ex, ensure_ascii=False, indent=2) +
        "\n\nTrả về JSON với các khóa: ma_sp, vat_lieu, xuat_xu, don_vi, "
        "gia_ton, he_so, ty_ren, met_dai_ty, ghi_chu, don_gia. "
        "Giá trị null nếu không xác định được."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
