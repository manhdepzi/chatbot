from __future__ import annotations

import os
import json
import hashlib
import mimetypes
import re
import unicodedata
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv

try:
    import psycopg
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - optional runtime dependency
    psycopg = None
    dict_row = None


load_dotenv()

_SEED_DONE = False


DEFAULT_COMPANY_SETTINGS = [
    ("company_name", "Kaiyo Vietnam", "Tên công ty dùng trong quy trình báo giá"),
    ("default_brand", "Kaiyo Viet Nam", "Thương hiệu/nhà sản xuất mặc định"),
    ("min_profit_pct", "0.12", "Tỷ lệ lợi nhuận tối thiểu"),
    ("default_vat_pct", "0.10", "VAT mặc định"),
    ("require_all_prices", "1", "Chặn xuất nếu còn thiếu giá"),
    ("require_no_unknown_products", "1", "Chặn xuất nếu còn sản phẩm chưa nhận diện"),
    ("require_human_approval", "1", "Bắt buộc phê duyệt trước khi phát hành"),
    ("quote_valid_days", "15", "Số ngày hiệu lực báo giá"),
    ("payment_terms", "Theo thỏa thuận", "Điều khoản thanh toán mặc định"),
    ("delivery_terms", "Theo tiến độ dự án", "Điều khoản giao hàng mặc định"),
]

DEFAULT_CATEGORIES = [
    ("SUPPLY_DUCT", "Ống gió cấp", ["supply duct", "s/a duct", "sa duct", "ống gió cấp", "cấp gió"], True),
    ("RETURN_DUCT", "Ống gió hồi", ["return duct", "r/a duct", "ra duct", "ống gió hồi", "hồi gió"], True),
    ("FRESH_AIR_DUCT", "Ống gió tươi", ["fresh air", "f/a duct", "fa duct", "cấp gió tươi", "gió tươi"], True),
    ("EXHAUST_AIR_DUCT", "Ống gió thải", ["exhaust air", "e/a duct", "ea duct", "thải gió", "hút thải", "hút mùi"], True),
    ("SMOKE_DUCT", "Ống gió hút khói", ["smoke duct", "smoke exhaust", "hút khói", "thải khói", "gió thải khói"], True),
    ("FIRE_DAMPER", "Van FD", ["fire damper", "fd", "van chặn lửa", "van dập lửa"], True),
    ("MOTORIZED_DAMPER", "Van MFD", ["motorized damper", "mfd", "md", "van mfd", "van điện"], True),
    ("VOLUME_CONTROL_DAMPER", "Van điều chỉnh lưu lượng", ["vcd", "obd", "van chỉnh lưu lượng", "van điều chỉnh"], True),
    ("BACK_DRAFT_DAMPER", "Van một chiều", ["back draft damper", "bdd", "van một chiều"], True),
    ("FLEXIBLE_DUCT", "Ống gió mềm", ["flexible duct", "ống gió mềm", "ống mềm"], False),
    ("FLEXIBLE_CONNECTOR", "Cổ bạt", ["flexible connector", "khớp nối mềm", "nối mềm", "cổ bạt"], False),
    ("REDUCER", "Côn thu", ["reducer", "côn thu", "ống thu", "côn giảm"], True),
    ("ELBOW", "Cút/co", ["elbow", "cút", "co", "góc", "cút 90", "cút 45"], True),
    ("TRANSITION", "Chuyển đổi", ["transition", "hộp chuyển", "gót giày", "chuyển đổi"], True),
    ("TEE", "Tê/chạc", ["tee", "ba ngã", "tê", "rẽ nhánh", "chạc"], True),
    ("CROSS", "Tứ ngã", ["cross", "tứ ngã", "chữ thập"], True),
    ("OFFSET", "Offset", ["offset", "lệch", "tránh dầm", "côn lệch"], True),
    ("ACCESS_DOOR", "Cửa thăm", ["access door", "cửa thăm", "cửa quan sát", "ad"], False),
    ("SILENCER", "Tiêu âm", ["silencer", "tiêu âm", "hộp tiêu âm", "ống tiêu âm"], True),
    ("PLENUM_BOX", "Hộp gió", ["plenum box", "hộp gió", "hộp gom gió", "plenum"], True),
    ("LINEAR_DIFFUSER", "Miệng gió linear", ["linear diffuser", "miệng gió slot", "slot diffuser"], False),
    ("SQUARE_DIFFUSER", "Miệng gió vuông", ["square diffuser", "miệng gió vuông", "khuyếch tán vuông", "cửa gió"], False),
    ("ROUND_DIFFUSER", "Miệng gió tròn", ["round diffuser", "miệng gió tròn", "khuyếch tán tròn"], False),
    ("EGGCRATE_GRILLE", "Miệng gió eggcrate", ["eggcrate grille", "lưới trứng"], False),
    ("JET_NOZZLE", "Miệng gió jet", ["jet nozzle", "miệng gió jet", "loa phun"], False),
    ("LOUVER", "Cửa gió Louver", ["louver", "cửa chớp", "cửa nan z", "oal", "fal", "eal"], False),
    ("ACCESSORY", "Phụ kiện treo", ["ty ren", "phụ kiện treo", "treo ống gió"], False),
    ("UNKNOWN", "Chưa xác định", ["unknown", "khác", "chưa rõ"], False),
]

DEFAULT_MATERIALS = [
    ("GI", "Tôn mạ kẽm"),
    ("SS", "Inox"),
    ("MS", "Thép đen"),
    ("PU", "Panel PU"),
]

DEFAULT_THICKNESS_RULES = [
    ("GI", 0, 300, 0.5),
    ("GI", 300, 750, 0.6),
    ("GI", 750, 1200, 0.75),
    ("GI", 1200, 1500, 1.0),
    ("GI", 1500, 99999, 1.2),
    ("SS", 0, 300, 0.5),
    ("SS", 300, 750, 0.6),
    ("SS", 750, 1200, 0.8),
    ("SS", 1200, 99999, 1.0),
]

DEFAULT_PRICE_ITEMS = [
    ("GI", 0.5, 12.0),
    ("GI", 0.6, 14.5),
    ("GI", 0.75, 17.5),
    ("GI", 1.0, 22.0),
    ("GI", 1.2, 26.5),
    ("SS", 0.5, 28.0),
    ("SS", 0.6, 32.0),
    ("SS", 0.8, 42.0),
    ("SS", 1.0, 50.0),
]

DEFAULT_COEFFICIENTS = [
    ("labor_rate_pct", 0.0, "percentage", "Nhân công theo % vật liệu"),
    ("installation_rate_pct", 0.0, "percentage", "Lắp đặt theo % vật liệu"),
    ("accessory_rate_pct", 0.0, "percentage", "Phụ kiện theo % vật liệu"),
    ("painting_rate_m2", 0.0, "fixed_per_m2", "Sơn theo m2"),
    ("insulation_rate_m2", 0.0, "fixed_per_m2", "Bảo ôn theo m2"),
    ("waste_rate_pct", 0.0, "percentage", "Hao hụt"),
    ("transportation_total", 0.0, "fixed_total", "Vận chuyển"),
    ("machinery_total", 0.0, "fixed_total", "Máy móc"),
    ("management_fee_pct", 0.0, "percentage", "Phí quản lý"),
    ("risk_fee_pct", 0.0, "percentage", "Phí rủi ro"),
    ("profit_pct", 0.0, "percentage", "Lợi nhuận"),
    ("vat_pct", 0.10, "percentage", "VAT"),
]

DEFAULT_MARK_PREFIXES = {
    "SUPPLY_DUCT": "SD",
    "RETURN_DUCT": "RD",
    "FRESH_AIR_DUCT": "FA",
    "EXHAUST_AIR_DUCT": "EA",
    "SMOKE_DUCT": "SMD",
    "FIRE_DAMPER": "FD",
    "MOTORIZED_DAMPER": "MD",
    "VOLUME_CONTROL_DAMPER": "VCD",
    "BACK_DRAFT_DAMPER": "BDD",
    "FLEXIBLE_DUCT": "FLD",
    "FLEXIBLE_CONNECTOR": "FC",
    "REDUCER": "RED",
    "ELBOW": "ELB",
    "TRANSITION": "TRN",
    "TEE": "TEE",
    "CROSS": "CRS",
    "OFFSET": "OFF",
    "ACCESS_DOOR": "AD",
    "SILENCER": "SIL",
    "PLENUM_BOX": "PB",
    "LINEAR_DIFFUSER": "LD",
    "SQUARE_DIFFUSER": "SDD",
    "ROUND_DIFFUSER": "RDD",
    "EGGCRATE_GRILLE": "EG",
    "JET_NOZZLE": "JN",
    "LOUVER": "LVR",
    "ACCESSORY": "ACC",
    "UNKNOWN": "UNK",
}

DEFAULT_PRODUCT_RULES = [
    ("SUPPLY_DUCT", "t", "area", "Tôn mạ kẽm theo độ dày", "Kaiyo Viet Nam", 0, 1.00, None, ""),
    ("RETURN_DUCT", "t", "area", "Tôn mạ kẽm theo độ dày", "Kaiyo Viet Nam", 0, 1.00, None, ""),
    ("FRESH_AIR_DUCT", "t", "area", "Tôn mạ kẽm theo độ dày", "Kaiyo Viet Nam", 0, 1.00, None, ""),
    ("EXHAUST_AIR_DUCT", "t", "area", "Tôn mạ kẽm theo độ dày", "Kaiyo Viet Nam", 0, 1.00, None, ""),
    ("SMOKE_DUCT", "t", "area", "Tôn mạ kẽm + EI", "Kaiyo Viet Nam", 0, 1.25, None, "EI duct multiplier seed"),
    ("REDUCER", "g", "area", "Tôn mạ kẽm theo độ dày", "Kaiyo Viet Nam", 0, 1.15, 500, "Tạm tính L=500"),
    ("TRANSITION", "vt", "area", "Tôn mạ kẽm theo độ dày", "Kaiyo Viet Nam", 0, 1.15, 500, "Tạm tính L=500"),
    ("ELBOW", "cv", "area", "Tôn mạ kẽm theo độ dày", "Kaiyo Viet Nam", 0, 1.35, None, ""),
    ("TEE", "tt", "area", "Tôn mạ kẽm theo độ dày", "Kaiyo Viet Nam", 0, 1.55, None, ""),
    ("CROSS", "cr", "area", "Tôn mạ kẽm theo độ dày", "Kaiyo Viet Nam", 0, 1.85, None, ""),
    ("PLENUM_BOX", "tb", "area", "Tôn mạ kẽm theo độ dày", "Kaiyo Viet Nam", 0, 1.20, 200, "Tạm tính L=200"),
    ("LOUVER", "c", "piece", "Nhôm sơn tĩnh điện", "Kaiyo Viet Nam", 350000, 1.00, None, "Cần cập nhật theo bảng giá NCC"),
    ("SQUARE_DIFFUSER", "c", "piece", "Nhôm sơn tĩnh điện", "Kaiyo Viet Nam", 450000, 1.00, None, "Cửa/miệng gió seed"),
    ("LINEAR_DIFFUSER", "ld", "piece", "Nhôm sơn tĩnh điện", "Kaiyo Viet Nam", 650000, 1.00, None, "Linear diffuser seed"),
    ("FIRE_DAMPER", "fd", "piece_area", "Thép mạ kẽm + EI", "Kaiyo Viet Nam", 900000, 1.00, 200, "L=200"),
    ("MOTORIZED_DAMPER", "mfd", "piece_area", "Tấm chống cháy + motor", "Kaiyo Viet Nam", 3000000, 1.00, 250, "Van MFD, L=250"),
    ("VOLUME_CONTROL_DAMPER", "vcd", "piece_area", "Thép mạ kẽm", "Kaiyo Viet Nam", 650000, 1.00, 200, "L=200"),
    ("BACK_DRAFT_DAMPER", "nrd", "piece_area", "Thép mạ kẽm", "Kaiyo Viet Nam", 700000, 1.00, 200, "L=200"),
    ("FLEXIBLE_CONNECTOR", "cb", "piece", "Cổ bạt", "Kaiyo Viet Nam", 250000, 1.00, 150, "Cổ bạt L=150"),
]

DEFAULT_PRODUCT_MASTER = [
    ("RECT_DUCT", "Ống gió vuông/chữ nhật", "SUPPLY_DUCT", "rule_area", ["width", "height", "length", "thickness", "material", "quantity"], "m"),
    ("RECT_ELBOW", "Cút/co vuông", "ELBOW", "rule_area", ["width", "height", "thickness", "material", "quantity"], "cai"),
    ("REDUCER", "Côn thu/giảm cấp", "REDUCER", "rule_area", ["width", "height", "width2", "height2", "length", "thickness", "material", "quantity"], "cai"),
    ("TEE_BRANCH", "Tê/chạc", "TEE", "rule_area", ["width", "height", "thickness", "material", "quantity"], "cai"),
    ("LOUVER", "Cửa gió Louver", "LOUVER", "rule_or_price_list", ["width", "height", "quantity"], "cai"),
    ("LOUVER_WITH_INSECT_SCREEN", "Cửa gió Louver kèm lưới chắn côn trùng", "LOUVER", "composite_rule", ["width", "height", "quantity"], "cai"),
    ("GRILLE_WITH_OBD", "Cửa/miệng gió kèm van OBD", "SQUARE_DIFFUSER", "composite_rule", ["width", "height", "quantity"], "cai"),
    ("OBD_DAMPER", "Van OBD/van điều chỉnh lưu lượng", "VOLUME_CONTROL_DAMPER", "rule_or_price_list", ["width", "height", "quantity"], "cai"),
    ("MANUAL_VOLUME_DAMPER", "Van gió điều chỉnh bằng tay", "VOLUME_CONTROL_DAMPER", "rule_or_price_list", ["width", "height", "quantity"], "cai"),
    ("MFD_L250", "Van MFD L250", "MOTORIZED_DAMPER", "rule_piece_area", ["width", "height", "quantity"], "cai"),
    ("FD_L250", "Van FD L250", "FIRE_DAMPER", "rule_piece_area", ["width", "height", "quantity"], "cai"),
    ("HANGER_ACCESSORY", "Ty ren/phụ kiện treo", "ACCESSORY", "manual_or_price_list", ["quantity"], "cai"),
]

DEFAULT_PRODUCT_ALIASES = [
    ("RECT_DUCT", "ống gió vuông", "vi", 100),
    ("RECT_DUCT", "rectangular duct", "en", 95),
    ("RECT_ELBOW", "cút vuông", "vi", 100),
    ("RECT_ELBOW", "co vuông", "vi", 95),
    ("RECT_ELBOW", "elbow", "en", 90),
    ("REDUCER", "côn thu", "vi", 100),
    ("REDUCER", "côn giảm", "vi", 95),
    ("REDUCER", "reducer", "en", 95),
    ("TEE_BRANCH", "tê", "vi", 90),
    ("TEE_BRANCH", "chạc 3", "vi", 100),
    ("LOUVER_WITH_INSECT_SCREEN", "cửa gió lcct", "vi", 98),
    ("LOUVER_WITH_INSECT_SCREEN", "cửa nan z lưới chắn côn trùng", "vi", 98),
    ("LOUVER_WITH_INSECT_SCREEN", "louver lcct", "vi", 98),
    ("LOUVER_WITH_INSECT_SCREEN", "louver grille with insect screen", "en", 98),
    ("LOUVER_WITH_INSECT_SCREEN", "防虫网百叶风口", "zh", 98),
    ("LOUVER_WITH_INSECT_SCREEN", "lưới chắn côn trùng", "vi", 80),
    ("LOUVER_WITH_INSECT_SCREEN", "lưới chống côn trùng", "vi", 80),
    ("LOUVER", "louver", "en", 95),
    ("LOUVER", "cửa nan z", "vi", 95),
    ("LOUVER", "cửa chớp", "vi", 95),
    ("GRILLE_WITH_OBD", "cửa gió obd", "vi", 98),
    ("GRILLE_WITH_OBD", "miệng gió obd", "vi", 98),
    ("GRILLE_WITH_OBD", "sag obd", "vi", 95),
    ("GRILLE_WITH_OBD", "eag obd", "vi", 95),
    ("OBD_DAMPER", "obd", "en", 95),
    ("OBD_DAMPER", "opposed blade damper", "en", 100),
    ("MANUAL_VOLUME_DAMPER", "van gió điều chỉnh bằng tay", "vi", 100),
    ("MANUAL_VOLUME_DAMPER", "van điều chỉnh lưu lượng bằng tay", "vi", 100),
    ("MANUAL_VOLUME_DAMPER", "vd", "vi", 85),
    ("MFD_L250", "van mfd", "vi", 100),
    ("MFD_L250", "motorized fire damper", "en", 100),
    ("FD_L250", "van fd", "vi", 100),
    ("FD_L250", "van chặn lửa", "vi", 95),
    ("HANGER_ACCESSORY", "ty ren", "vi", 100),
    ("HANGER_ACCESSORY", "phụ kiện treo", "vi", 95),
]


def requested() -> bool:
    return os.getenv("USE_SUPABASE_DB", "1").strip().lower() not in {"0", "false", "no", "off"}


def enabled() -> bool:
    database_url = os.getenv("DATABASE_URL", "")
    if not requested():
        return False
    return bool(database_url and "[YOUR-PASSWORD]" not in database_url and psycopg is not None)


def strict_enabled() -> bool:
    return requested()


@contextmanager
def connect():
    if not enabled():
        raise RuntimeError("Supabase/PostgreSQL is not configured.")
    conn = psycopg.connect(
        os.getenv("DATABASE_URL"),
        row_factory=dict_row,
        prepare_threshold=None,
        connect_timeout=int(os.getenv("SUPABASE_CONNECT_TIMEOUT", "10") or 10),
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def company_id(conn) -> str:
    name = os.getenv("COMPANY_NAME", "Kaiyo Việt Nam")
    row = conn.execute("select id from public.companies where name = %s order by created_at limit 1", (name,)).fetchone()
    if row:
        return str(row["id"])
    row = conn.execute("insert into public.companies (name) values (%s) returning id", (name,)).fetchone()
    return str(row["id"])


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("đ", "d").replace("Đ", "D")
    text = re.sub(r"[\+\-/_,;:()\[\]{}|]+", " ", text)
    return re.sub(r"\s+", " ", text.lower()).strip()


def normalize_unit(unit: Any) -> str:
    raw = str(unit or "").strip().lower()
    raw = raw.replace("²", "2").replace("đ", "d")
    normalized = unicodedata.normalize("NFKD", raw)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    compact = re.sub(r"[^a-z0-9]+", "", normalized)
    compact = re.sub(r"^\d+", "", compact)
    aliases = {
        "cai": "cai",
        "cua": "cai",
        "chiec": "cai",
        "donvi": "cai",
        "pcs": "cai",
        "pc": "cai",
        "piece": "cai",
        "pieces": "cai",
        "ea": "cai",
        "each": "cai",
        "set": "bo",
        "sets": "bo",
        "bo": "bo",
        "lo": "lo",
        "ong": "ong",
        "cuon": "cuon",
        "met": "m",
        "metvuong": "m2",
        "sqm": "m2",
        "m": "m",
        "m2": "m2",
        "tan": "tan",
        "kg": "kg",
        "chuyen": "chuyen",
    }
    return aliases.get(compact, compact or "cai")


def ensure_seed_data() -> None:
    global _SEED_DONE
    if not enabled():
        return
    if _SEED_DONE:
        return
    with connect() as conn:
        _ensure_runtime_tables(conn)
        cid = company_id(conn)
        _seed_units(conn)
        category_ids: Dict[str, Any] = {}
        for key, display_name, keywords, requires_dimensions in DEFAULT_CATEGORIES:
            row = conn.execute(
                """
                insert into public.product_categories
                (company_id, key, display_name_vi, display_name_en, keywords, requires_dimensions, active)
                values (%s, %s, %s, %s, %s, %s, true)
                on conflict (company_id, key)
                do update set display_name_vi = excluded.display_name_vi,
                              display_name_en = excluded.display_name_en,
                              keywords = excluded.keywords,
                              requires_dimensions = excluded.requires_dimensions,
                              active = true
                returning id
                """,
                (cid, key, display_name, key, keywords, requires_dimensions),
            ).fetchone()
            category_ids[key] = row["id"]

        material_ids: Dict[str, Any] = {}
        for code, name_vi in DEFAULT_MATERIALS:
            row = conn.execute(
                """
                insert into public.materials (company_id, code, name_vi)
                values (%s, %s, %s)
                on conflict (company_id, code) do update set name_vi = excluded.name_vi, active = true
                returning id
                """,
                (cid, code, name_vi),
            ).fetchone()
            material_ids[code] = row["id"]

        for material, min_size, max_size, thickness in DEFAULT_THICKNESS_RULES:
            material_id = material_ids.get(material)
            if not material_id:
                continue
            conn.execute(
                """
                insert into public.thickness_rules
                (company_id, material_id, min_size_mm, max_size_mm, thickness_mm, approval_status)
                select %s, %s, %s, %s, %s, 'approved'
                where not exists (
                  select 1 from public.thickness_rules
                  where company_id = %s and material_id = %s
                    and min_size_mm = %s and max_size_mm = %s and thickness_mm = %s
                )
                """,
                (cid, material_id, min_size, max_size, thickness, cid, material_id, min_size, max_size, thickness),
            )

        for key, value, value_type, description in DEFAULT_COEFFICIENTS:
            conn.execute(
                """
                insert into public.coefficient_rules
                (company_id, key, value, value_type, approval_status, description)
                values (%s, %s, %s, %s, 'approved', %s)
                on conflict (company_id, key)
                do update set value = public.coefficient_rules.value
                """,
                (cid, key, value, value_type, description),
            )

        for key, value, description in DEFAULT_COMPANY_SETTINGS:
            conn.execute(
                """
                insert into public.company_settings (company_id, key, value, description)
                values (%s, %s, %s, %s)
                on conflict (company_id, key)
                do update set description = coalesce(public.company_settings.description, excluded.description)
                """,
                (cid, key, value, description),
            )

        price_list = conn.execute(
            """
            insert into public.price_lists (company_id, name, region, currency, is_default, approval_status, notes)
            values (%s, 'Bảng giá HVAC mặc định', 'Default', 'VND', true, 'approved', 'Seed Supabase')
            on conflict (company_id, name)
            do update set is_default = true, approval_status = 'approved'
            returning id
            """,
            (cid,),
        ).fetchone()
        price_list_id = price_list["id"]
        for material, thickness, unit_price in DEFAULT_PRICE_ITEMS:
            material_id = material_ids.get(material)
            if material_id:
                conn.execute(
                    """
                    insert into public.price_list_items
                    (price_list_id, material_id, thickness_mm, unit, unit_price, source_note)
                    select %s, %s, %s, 'm2', %s, 'Seed Supabase'
                    where not exists (
                      select 1 from public.price_list_items
                      where price_list_id = %s and material_id = %s
                        and thickness_mm = %s and unit = 'm2'
                    )
                    """,
                    (price_list_id, material_id, thickness, unit_price, price_list_id, material_id, thickness),
                )

        for category, quote_code, unit_mode, material_spec, brand, base_unit_price, area_multiplier, length_mm, note in DEFAULT_PRODUCT_RULES:
            category_id = category_ids.get(category)
            if category_id:
                conn.execute(
                    """
                    insert into public.product_pricing_rules
                    (company_id, category_id, quote_code, unit_mode, material_spec, brand,
                     base_unit_price, area_multiplier, length_mm, formula_json, approval_status, notes)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb, 'approved', %s)
                    on conflict (company_id, category_id)
                    do update set quote_code = excluded.quote_code,
                                  unit_mode = excluded.unit_mode,
                                  material_spec = excluded.material_spec,
                                  brand = excluded.brand,
                                  base_unit_price = excluded.base_unit_price,
                                  area_multiplier = excluded.area_multiplier,
                                  length_mm = excluded.length_mm,
                                  notes = excluded.notes,
                                  approval_status = 'approved'
                    """,
                    (cid, category_id, quote_code, unit_mode, material_spec, brand, base_unit_price, area_multiplier, length_mm, note),
                )

        for product_code, display_name_vi, category, pricing_method, required_fields, default_unit in DEFAULT_PRODUCT_MASTER:
            category_id = category_ids.get(category)
            row = conn.execute(
                """
                insert into public.product_master
                (company_id, product_code, display_name_vi, category_id, category_key,
                 pricing_method, required_fields, default_unit, active)
                values (%s, %s, %s, %s, %s, %s, %s, %s, true)
                on conflict (company_id, product_code)
                do update set display_name_vi = excluded.display_name_vi,
                              category_id = excluded.category_id,
                              category_key = excluded.category_key,
                              pricing_method = excluded.pricing_method,
                              required_fields = excluded.required_fields,
                              default_unit = excluded.default_unit,
                              active = true
                returning id
                """,
                (cid, product_code, display_name_vi, category_id, category, pricing_method, required_fields, default_unit),
            ).fetchone()
            for alias_product, alias_text, language, confidence in DEFAULT_PRODUCT_ALIASES:
                if alias_product == product_code:
                    conn.execute(
                        """
                        insert into public.product_aliases
                        (company_id, product_id, product_code, alias_text, normalized_alias,
                         language, confidence, approval_status, source)
                        values (%s, %s, %s, %s, %s, %s, %s, 'approved', 'seed')
                        on conflict (company_id, normalized_alias, product_code)
                        do update set alias_text = excluded.alias_text,
                                      product_id = excluded.product_id,
                                      confidence = greatest(public.product_aliases.confidence, excluded.confidence),
                                      approval_status = 'approved'
                        """,
                        (cid, row["id"], product_code, alias_text, normalize_text(alias_text), language, confidence),
                    )
    _SEED_DONE = True


def _ensure_runtime_tables(conn) -> None:
    conn.execute(
        """
        create table if not exists public.company_settings (
          id uuid primary key default gen_random_uuid(),
          company_id uuid not null references public.companies(id) on delete cascade,
          key text not null,
          value text not null,
          description text,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now(),
          unique (company_id, key)
        )
        """
    )
    conn.execute(
        """
        create table if not exists public.product_master (
          id uuid primary key default gen_random_uuid(),
          company_id uuid not null references public.companies(id) on delete cascade,
          product_code text not null,
          display_name_vi text not null,
          category_id uuid references public.product_categories(id),
          category_key text,
          pricing_method text not null,
          required_fields text[] not null default '{}',
          default_unit text,
          active boolean not null default true,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now(),
          unique (company_id, product_code)
        )
        """
    )
    conn.execute(
        """
        create table if not exists public.product_aliases (
          id uuid primary key default gen_random_uuid(),
          company_id uuid not null references public.companies(id) on delete cascade,
          product_id uuid references public.product_master(id) on delete cascade,
          product_code text not null,
          alias_text text not null,
          normalized_alias text not null,
          language text,
          confidence numeric(5,2) not null default 100,
          approval_status public.approval_status not null default 'approved',
          source text,
          approved_by uuid references public.profiles(id),
          created_at timestamptz not null default now(),
          unique (company_id, normalized_alias, product_code)
        )
        """
    )
    conn.execute(
        """
        create table if not exists public.sales_product_answers (
          id uuid primary key default gen_random_uuid(),
          company_id uuid not null references public.companies(id) on delete cascade,
          session_id uuid references public.quotation_sessions(id) on delete set null,
          line_signature text not null,
          description text not null,
          product_code text,
          category_key text,
          unit_price numeric(18,2),
          unit text,
          note text,
          approved_by uuid references public.profiles(id),
          created_at timestamptz not null default now()
        )
        """
    )


def _seed_units(conn) -> None:
    for code, name_vi, base_code, is_package in [
        ("m", "mét", None, False),
        ("m2", "mét vuông", None, False),
        ("cai", "cái", None, False),
        ("bo", "bộ", None, False),
        ("lo", "lô", None, False),
        ("ong", "ống", "m", True),
        ("cuon", "cuộn", "m", True),
        ("tan", "tấn", None, False),
        ("kg", "kg", None, False),
        ("chuyen", "chuyến", None, False),
    ]:
        conn.execute(
            """
            insert into public.units (code, name_vi, base_code, is_package)
            values (%s, %s, %s, %s)
            on conflict (code) do nothing
            """,
            (code, name_vi, base_code, is_package),
        )


def supabase_url() -> str:
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[: -len("/rest/v1")]
    return url


def service_role_key() -> str:
    return os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()


def storage_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    key = service_role_key()
    headers = {
        "Authorization": f"Bearer {key}",
        "apikey": key,
    }
    if extra:
        headers.update(extra)
    return headers


def ensure_storage_bucket(bucket: Optional[str] = None) -> str:
    bucket_name = bucket or os.getenv("SUPABASE_STORAGE_BUCKET", "quotation-files")
    url = supabase_url()
    key = service_role_key()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL hoặc SUPABASE_SERVICE_ROLE_KEY chưa được cấu hình.")

    response = requests.get(
        f"{url}/storage/v1/bucket/{bucket_name}",
        headers=storage_headers(),
        timeout=30,
    )
    if response.status_code == 200:
        return bucket_name
    if response.status_code not in {400, 404}:
        response.raise_for_status()

    create_response = requests.post(
        f"{url}/storage/v1/bucket",
        headers=storage_headers({"Content-Type": "application/json"}),
        json={"id": bucket_name, "name": bucket_name, "public": False},
        timeout=30,
    )
    if create_response.status_code not in {200, 201, 409}:
        create_response.raise_for_status()
    return bucket_name


def _storage_safe_filename(file_name: str) -> str:
    path = Path(file_name.replace("\\", "/"))
    stem = path.stem or "file"
    suffix = path.suffix.lower()
    normalized = unicodedata.normalize("NFKD", stem.replace("đ", "d").replace("Đ", "D"))
    ascii_stem = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_stem).strip(".-_")
    if not ascii_stem:
        ascii_stem = hashlib.sha1(file_name.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{ascii_stem[:90]}{suffix}"


def upload_project_file(
    file_name: str,
    data: bytes,
    kind: str,
    project: str = "",
    customer: str = "",
    mime_type: Optional[str] = None,
) -> Dict[str, Any]:
    if not enabled():
        return {}

    bucket = ensure_storage_bucket()
    url = supabase_url()
    checksum = hashlib.sha256(data).hexdigest()
    safe_name = _storage_safe_filename(file_name)
    today = datetime.utcnow().strftime("%Y/%m/%d")
    storage_path = f"{today}/{kind}/{checksum[:12]}-{safe_name}"
    guessed_mime = mime_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    response = requests.post(
        f"{url}/storage/v1/object/{bucket}/{storage_path}",
        headers=storage_headers({
            "Content-Type": guessed_mime,
            "x-upsert": "true",
        }),
        data=data,
        timeout=120,
    )
    if response.status_code not in {200, 201}:
        response.raise_for_status()

    with connect() as conn:
        cid = company_id(conn)
        customer_id = get_or_create_customer(conn, cid, customer) if customer else None
        project_id = get_or_create_project(conn, cid, project, customer_id) if project else None
        row = conn.execute(
            """
            insert into public.project_files
            (company_id, project_id, kind, file_name, storage_bucket, storage_path,
             mime_type, file_size, checksum)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning id::text
            """,
            (
                cid,
                project_id,
                kind,
                file_name,
                bucket,
                storage_path,
                guessed_mime,
                len(data),
                checksum,
            ),
        ).fetchone()

    return {
        "id": row["id"],
        "bucket": bucket,
        "path": storage_path,
        "checksum": checksum,
        "file_name": file_name,
        "kind": kind,
    }


def list_price_lists() -> List[Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select pl.id::text as id, pl.name, coalesce(s.name, '') as supplier, pl.region,
                   coalesce(c.name, '') as customer, pl.valid_from::text, pl.valid_to::text,
                   pl.notes, pl.is_default
            from public.price_lists pl
            left join public.suppliers s on s.id = pl.supplier_id
            left join public.customers c on c.id = pl.customer_id
            where pl.company_id = %s
            order by pl.is_default desc, pl.name
            """,
            (cid,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_price_list(name: str, supplier: str = "", region: str = "", customer: str = "", notes: str = "") -> str:
    with connect() as conn:
        cid = company_id(conn)
        row = conn.execute(
            """
            insert into public.price_lists (company_id, name, region, notes, approval_status)
            values (%s, %s, %s, %s, 'approved')
            on conflict (company_id, name)
            do update set region = excluded.region, notes = excluded.notes
            returning id::text
            """,
            (cid, name, region, notes),
        ).fetchone()
    return str(row["id"])


def get_price_rules_for_list(price_list_id: Optional[str]) -> Dict[Tuple[str, float], float]:
    with connect() as conn:
        cid = company_id(conn)
        if price_list_id:
            rows = conn.execute(
                """
                select m.code as material, pli.thickness_mm as thickness, pli.unit_price
                from public.price_list_items pli
                join public.price_lists pl on pl.id = pli.price_list_id
                left join public.materials m on m.id = pli.material_id
                where pl.company_id = %s and pli.price_list_id = %s
                """,
                (cid, price_list_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                select m.code as material, pli.thickness_mm as thickness, pli.unit_price
                from public.price_list_items pli
                join public.price_lists pl on pl.id = pli.price_list_id
                left join public.materials m on m.id = pli.material_id
                where pl.company_id = %s and pl.is_default = true
                order by pl.created_at desc
                """,
                (cid,),
            ).fetchall()
    return {
        (row["material"], float(row["thickness"] or 0)): float(row["unit_price"] or 0)
        for row in rows
        if row.get("material") and row.get("thickness") is not None
    }


def upsert_price_list_item(
    price_list_id: str,
    material: str,
    thickness: float,
    unit_price: float,
    unit: str = "m2",
    source: str = "User",
) -> None:
    with connect() as conn:
        cid = company_id(conn)
        material_row = conn.execute(
            """
            insert into public.materials (company_id, code, name_vi)
            values (%s, %s, %s)
            on conflict (company_id, code) do update set name_vi = excluded.name_vi
            returning id
            """,
            (cid, material, material),
        ).fetchone()
        conn.execute(
            """
            insert into public.units (code, name_vi)
            values (%s, %s)
            on conflict (code) do nothing
            """,
            (normalize_unit(unit), unit),
        )
        conn.execute(
            """
            insert into public.price_list_items
            (price_list_id, material_id, thickness_mm, unit, unit_price, source_note)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (price_list_id, material_row["id"], thickness, normalize_unit(unit), unit_price, source),
        )


def get_company_settings() -> Dict[str, str]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select key, value
            from public.company_settings
            where company_id = %s
            """,
            (cid,),
        ).fetchall()
    return {row["key"]: row["value"] for row in rows}


def list_company_settings() -> List[Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select key, value, description
            from public.company_settings
            where company_id = %s
            order by key
            """,
            (cid,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_company_setting(key: str, value: str, description: str = "") -> None:
    with connect() as conn:
        cid = company_id(conn)
        conn.execute(
            """
            insert into public.company_settings (company_id, key, value, description)
            values (%s, %s, %s, %s)
            on conflict (company_id, key)
            do update set value = excluded.value,
                          description = coalesce(nullif(excluded.description, ''), public.company_settings.description),
                          updated_at = now()
            """,
            (cid, key, value, description),
        )


def get_category_keywords() -> List[Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select key as normalized_key, keywords
            from public.product_categories
            where company_id = %s and active = true
            order by key
            """,
            (cid,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_mark_prefixes() -> Dict[str, str]:
    return dict(DEFAULT_MARK_PREFIXES)


def get_thickness_rules() -> List[Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select m.code as material, tr.min_size_mm as min_width,
                   tr.max_size_mm as max_width, tr.thickness_mm as thickness
            from public.thickness_rules tr
            join public.materials m on m.id = tr.material_id
            where tr.company_id = %s and tr.approval_status = 'approved'
            order by m.code, tr.min_size_mm
            """,
            (cid,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_product_master() -> List[Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select product_code, display_name_vi, category_key as category,
                   pricing_method, array_to_string(required_fields, ',') as required_fields,
                   default_unit, active
            from public.product_master
            where company_id = %s
            order by product_code
            """,
            (cid,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_product_aliases(limit: int = 500) -> List[Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select pa.alias_text, pa.product_code, pm.display_name_vi,
                   pm.category_key as category, pa.language, pa.confidence,
                   pa.approval_status as status, pa.source, pa.created_at
            from public.product_aliases pa
            left join public.product_master pm on pm.id = pa.product_id
            where pa.company_id = %s
            order by pa.created_at desc, pa.confidence desc
            limit %s
            """,
            (cid, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def product_alias_match_rows() -> List[Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select pa.alias_text, pa.normalized_alias, pa.product_code, pa.confidence,
                   pm.display_name_vi, pm.category_key as category,
                   pm.pricing_method, array_to_string(pm.required_fields, ',') as required_fields,
                   pm.default_unit
            from public.product_aliases pa
            join public.product_master pm on pm.id = pa.product_id
            where pa.company_id = %s
              and pa.approval_status = 'approved'
              and pm.active = true
            """,
            (cid,),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_product_alias(
    product_code: str,
    alias_text: str,
    language: str = "",
    confidence: float = 100,
    source: str = "manual",
) -> None:
    if not product_code or not alias_text:
        return
    with connect() as conn:
        cid = company_id(conn)
        product = conn.execute(
            """
            select id
            from public.product_master
            where company_id = %s and product_code = %s
            """,
            (cid, product_code),
        ).fetchone()
        if not product:
            raise ValueError(f"Không tìm thấy product_code trên Supabase: {product_code}")
        conn.execute(
            """
            insert into public.product_aliases
            (company_id, product_id, product_code, alias_text, normalized_alias,
             language, confidence, approval_status, source)
            values (%s, %s, %s, %s, %s, %s, %s, 'approved', %s)
            on conflict (company_id, normalized_alias, product_code)
            do update set alias_text = excluded.alias_text,
                          language = excluded.language,
                          confidence = greatest(public.product_aliases.confidence, excluded.confidence),
                          approval_status = 'approved',
                          source = excluded.source
            """,
            (cid, product["id"], product_code, alias_text, normalize_text(alias_text), language, confidence, source),
        )


def save_sales_product_answer(
    item: Dict[str, Any],
    product_code: str = "",
    category: str = "",
    unit_price: float = 0.0,
    unit: str = "",
    note: str = "",
    approved_by: str = "",
    save_alias: bool = False,
    signature: str = "",
) -> None:
    with connect() as conn:
        cid = company_id(conn)
        conn.execute(
            """
            insert into public.sales_product_answers
            (company_id, line_signature, description, product_code, category_key,
             unit_price, unit, note)
            values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                cid,
                signature or hashlib.sha1(normalize_text(item).encode("utf-8")).hexdigest(),
                str(item.get("description") or ""),
                product_code,
                category,
                float(unit_price or 0),
                normalize_unit(unit or item.get("unit")),
                f"{approved_by}: {note}".strip(": "),
            ),
        )
    if save_alias and product_code:
        upsert_product_alias(product_code, str(item.get("description") or ""), "auto", 92, "sales_confirmed")


def list_sales_answers(limit: int = 200) -> List[Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select description, product_code, category_key as category,
                   unit_price, unit, note, '' as approved_by, created_at
            from public.sales_product_answers
            where company_id = %s
            order by created_at desc
            limit %s
            """,
            (cid, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_or_create_customer(conn, cid: str, name: str):
    name = (name or "Khách hàng chưa đặt tên").strip()
    row = conn.execute(
        """
        insert into public.customers (company_id, name)
        values (%s, %s)
        on conflict (company_id, name) do update set name = excluded.name
        returning id
        """,
        (cid, name),
    ).fetchone()
    return row["id"]


def get_or_create_project(conn, cid: str, name: str, customer_id=None):
    name = (name or "Dự án chưa đặt tên").strip()
    row = conn.execute(
        """
        select id from public.projects
        where company_id = %s and name = %s
        order by created_at desc
        limit 1
        """,
        (cid, name),
    ).fetchone()
    if row:
        return row["id"]
    row = conn.execute(
        """
        insert into public.projects (company_id, customer_id, name)
        values (%s, %s, %s)
        returning id
        """,
        (cid, customer_id, name),
    ).fetchone()
    return row["id"]


def save_quotation_session(
    name: str,
    items: List[Dict[str, Any]],
    summary: Dict[str, Any],
    coefficients: Dict[str, Any],
    price_overrides: Dict[Tuple[str, float], float],
    customer: str = "",
    project: str = "",
    source_file: str = "",
    price_list_id: Optional[str] = None,
) -> str:
    with connect() as conn:
        cid = company_id(conn)
        customer_id = get_or_create_customer(conn, cid, customer) if customer else None
        project_id = get_or_create_project(conn, cid, project, customer_id) if project else None
        session = conn.execute(
            """
            insert into public.quotation_sessions
            (company_id, project_id, customer_id, price_list_id, name, status, summary_json, warnings_json)
            values (%s, %s, %s, %s, %s, 'draft', %s, %s)
            returning id::text
            """,
            (
                cid,
                project_id,
                customer_id,
                price_list_id,
                name,
                json.dumps({
                    **summary,
                    "_coefficients": coefficients,
                    "_price_overrides": {f"{m}|{t}": p for (m, t), p in price_overrides.items()},
                    "_source_file": source_file,
                }, ensure_ascii=False),
                json.dumps([], ensure_ascii=False),
            ),
        ).fetchone()
        session_id = session["id"]
        for idx, item in enumerate(items, start=1):
            category_id = _category_id(conn, cid, item.get("category"))
            material_id = _material_id(conn, cid, item.get("material"))
            conn.execute(
                """
                insert into public.quotation_items
                (session_id, line_no, source_row, mark, description, category_id, material_id,
                 width_mm, height_mm, diameter_mm, length_mm, thickness_mm,
                 quantity, unit, quote_quantity, quote_unit, quote_unit_price,
                 line_total, price_source, warning_json, raw_json)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    idx,
                    item.get("source_row"),
                    item.get("mark"),
                    item.get("description"),
                    category_id,
                    material_id,
                    item.get("width"),
                    item.get("height"),
                    item.get("diameter"),
                    item.get("length"),
                    item.get("thickness"),
                    item.get("quantity") or 0,
                    normalize_unit(item.get("unit")),
                    item.get("quote_output_quantity") or item.get("quantity"),
                    normalize_unit(item.get("quote_output_unit") or item.get("unit")),
                    item.get("quote_output_unit_price") or item.get("quote_unit_price"),
                    item.get("subtotal") or item.get("grand_total"),
                    _price_source(item),
                    json.dumps(item.get("warnings") or [], ensure_ascii=False),
                    json.dumps(item, ensure_ascii=False, default=str),
                ),
            )
    return session_id


def list_quotation_sessions(limit: int = 30) -> List[Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select qs.id::text as id, qs.name, coalesce(c.name, '') as customer,
                   coalesce(p.name, '') as project,
                   coalesce(qs.summary_json->>'_source_file', '') as source_file,
                   qs.created_at, qs.updated_at
            from public.quotation_sessions qs
            left join public.customers c on c.id = qs.customer_id
            left join public.projects p on p.id = qs.project_id
            where qs.company_id = %s
            order by qs.created_at desc
            limit %s
            """,
            (cid, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def load_quotation_session(session_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            """
            select qs.*, coalesce(c.name, '') as customer, coalesce(p.name, '') as project
            from public.quotation_sessions qs
            left join public.customers c on c.id = qs.customer_id
            left join public.projects p on p.id = qs.project_id
            where qs.id = %s
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return None
        item_rows = conn.execute(
            """
            select raw_json
            from public.quotation_items
            where session_id = %s
            order by line_no
            """,
            (session_id,),
        ).fetchall()
    summary = dict(row["summary_json"] or {})
    coefficients = summary.pop("_coefficients", {})
    encoded_prices = summary.pop("_price_overrides", {})
    source_file = summary.pop("_source_file", "")
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "customer": row["customer"],
        "project": row["project"],
        "source_file": source_file,
        "summary": summary,
        "coefficients": coefficients,
        "price_overrides": {
            (material, float(thickness)): price
            for key, price in encoded_prices.items()
            for material, thickness in [key.split("|", 1)]
        },
        "items": [dict(item["raw_json"]) for item in item_rows],
    }


def save_approval(
    status: str,
    project: str,
    customer: str,
    approver: str,
    notes: str,
    readiness_score: float,
    session_id: Optional[str] = None,
) -> str:
    with connect() as conn:
        cid = company_id(conn)
        target_session_id = session_id
        if not target_session_id:
            target_session_id = _latest_session_id(conn, cid, project, customer)
        if not target_session_id:
            customer_id = get_or_create_customer(conn, cid, customer) if customer else None
            project_id = get_or_create_project(conn, cid, project, customer_id) if project else None
            row = conn.execute(
                """
                insert into public.quotation_sessions
                (company_id, project_id, customer_id, name, status, readiness_score)
                values (%s, %s, %s, %s, 'pending_approval', %s)
                returning id::text
                """,
                (cid, project_id, customer_id, project or "Phiên phê duyệt", readiness_score),
            ).fetchone()
            target_session_id = row["id"]
        approval_status = "approved" if status == "APPROVED" else "rejected" if status == "REJECTED" else "pending"
        row = conn.execute(
            """
            insert into public.quotation_approvals
            (session_id, status, notes, readiness_score)
            values (%s, %s, %s, %s)
            returning id::text
            """,
            (target_session_id, approval_status, f"{approver}: {notes}".strip(": "), readiness_score),
        ).fetchone()
    return str(row["id"])


def list_approvals(limit: int = 50) -> List[Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select qa.id::text as id, qa.session_id::text as session_id,
                   coalesce(p.name, '') as project, coalesce(c.name, '') as customer,
                   upper(qa.status::text) as status, '' as approver, qa.notes,
                   qa.readiness_score, qa.created_at
            from public.quotation_approvals qa
            join public.quotation_sessions qs on qs.id = qa.session_id
            left join public.projects p on p.id = qs.project_id
            left join public.customers c on c.id = qs.customer_id
            where qs.company_id = %s
            order by qa.created_at desc
            limit %s
            """,
            (cid, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def has_final_approval(project: str, customer: str) -> bool:
    with connect() as conn:
        cid = company_id(conn)
        row = conn.execute(
            """
            select qa.id
            from public.quotation_approvals qa
            join public.quotation_sessions qs on qs.id = qa.session_id
            left join public.projects p on p.id = qs.project_id
            left join public.customers c on c.id = qs.customer_id
            where qs.company_id = %s
              and coalesce(p.name, '') = %s
              and coalesce(c.name, '') = %s
              and qa.status = 'approved'
            order by qa.created_at desc
            limit 1
            """,
            (cid, project, customer),
        ).fetchone()
    return bool(row)


def get_coefficient_rules() -> Dict[str, Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select key, value, value_type
            from public.coefficient_rules
            where company_id = %s and approval_status = 'approved'
            """,
            (cid,),
        ).fetchall()
    return {row["key"]: {"value": float(row["value"]), "type": row["value_type"]} for row in rows}


def get_pricing_rules() -> Dict[Tuple[str, float], float]:
    return get_price_rules_for_list(None)


def get_product_pricing_rules() -> Dict[str, Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select pc.key as category, ppr.quote_code, ppr.unit_mode, ppr.material_spec,
                   ppr.brand, ppr.base_unit_price, ppr.area_multiplier, ppr.length_mm,
                   ppr.notes as note, 'Supabase' as source
            from public.product_pricing_rules ppr
            join public.product_categories pc on pc.id = ppr.category_id
            where ppr.company_id = %s and ppr.approval_status = 'approved'
            """,
            (cid,),
        ).fetchall()
    return {row["category"]: dict(row) for row in rows}


def get_approved_material_memory() -> List[Dict[str, Any]]:
    """Return manufacturing specifications from approved completed quotations only."""
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select pc.key as category,
                   qmi.normalized_description,
                   qmi.width_mm as width,
                   qmi.height_mm as height,
                   qmi.diameter_mm as diameter,
                   qmi.length_mm as length,
                   qmi.thickness_mm as thickness,
                   m.code as material,
                   qmi.material_spec as quote_material_spec,
                   qmi.note as quote_note,
                   qms.source_file_name as source_file
            from public.quote_memory_items qmi
            join public.quote_memory_sources qms on qms.id = qmi.source_id
            left join public.product_categories pc on pc.id = qmi.category_id
            left join public.materials m on m.id = qmi.material_id
            where qmi.company_id = %s
              and qmi.approval_status = 'approved'
              and nullif(trim(qmi.material_spec), '') is not null
            order by qmi.created_at desc
            """,
            (cid,),
        ).fetchall()

    learned_rows: List[Dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        _decode_memory_formula(data)
        learned_rows.append(data)
    return learned_rows


def train_quote_memory(
    reference_items: Iterable[Dict[str, Any]],
    signature_func,
    description_key_func,
    source_project: str = "",
    source_customer: str = "",
    source_file: str = "",
) -> int:
    with connect() as conn:
        cid = company_id(conn)
        source_name = source_file or "Manual AI training"
        source = conn.execute(
            """
            insert into public.quote_memory_sources
            (company_id, source_file_name, approval_status, notes)
            values (%s, %s, 'approved', 'Created from app training')
            returning id
            """,
            (cid, source_name),
        ).fetchone()
        source_id = source["id"]
        count = 0
        signature_occurrences: Dict[str, int] = {}
        for item in reference_items:
            unit_price = float(item.get("reference_unit_price") or item.get("quote_unit_price") or 0)
            if unit_price <= 0:
                continue
            if str(item.get("category") or "UNKNOWN") == "UNKNOWN":
                continue
            if item.get("llm_enriched"):
                llm_confidence = float(item.get("llm_confidence") or 0)
                if llm_confidence < 85 or not item.get("product_code"):
                    continue
            base_signature = signature_func(item)
            signature_occurrences[base_signature] = signature_occurrences.get(base_signature, 0) + 1
            occurrence = signature_occurrences[base_signature]
            signature = base_signature if occurrence == 1 else f"{base_signature}:occurrence:{occurrence}"
            category_id = _category_id(conn, cid, item.get("category"))
            unit_code = normalize_unit(item.get("unit"))
            conn.execute(
                """
                insert into public.units (code, name_vi)
                values (%s, %s)
                on conflict (code) do nothing
                """,
                (unit_code, str(item.get("unit") or unit_code)),
            )
            conn.execute(
                """
                insert into public.quote_memory_items
                (company_id, source_id, signature, normalized_description, category_id,
                 width_mm, height_mm, diameter_mm, length_mm, unit, quote_unit_price,
                 material_spec, brand, note, approval_status)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'approved')
                on conflict (source_id, signature)
                do update set quote_unit_price = excluded.quote_unit_price,
                              material_spec = excluded.material_spec,
                              brand = excluded.brand,
                              note = excluded.note,
                              unit = excluded.unit,
                              approval_status = 'approved'
                """,
                (
                    cid,
                    source_id,
                    signature,
                    description_key_func(item),
                    category_id,
                    item.get("width"),
                    item.get("height"),
                    item.get("diameter"),
                    item.get("length"),
                    unit_code,
                    unit_price,
                    item.get("quote_material_spec"),
                    item.get("quote_brand"),
                    _memory_note_with_formula(item),
                ),
            )
            count += 1
    return count


def quote_memory_rows(
    signatures: List[str],
    description_units: List[Tuple[str, str]],
    descriptions: List[str],
    source_file: str = "",
) -> List[Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        params: List[Any] = [cid]
        source_clause = ""
        if source_file:
            source_clause = "and qms.source_file_name = %s"
            params.append(source_file)
        rows = conn.execute(
            f"""
            select qmi.id::text as memory_id, qmi.signature, qmi.normalized_description,
                   qmi.unit, qmi.quote_unit_price, qmi.material_spec as quote_material_spec,
                   qmi.brand as quote_brand, qmi.note as quote_note,
                   '' as source_project, '' as source_customer,
                   qms.source_file_name as source_file, 1 as trained_count, qmi.created_at as updated_at
            from public.quote_memory_items qmi
            join public.quote_memory_sources qms on qms.id = qmi.source_id
            where qmi.company_id = %s
              and qmi.approval_status = 'approved'
              {source_clause}
            """,
            tuple(params),
        ).fetchall()

    signature_set = set(signatures)
    description_set = set(descriptions)
    description_unit_set = {(desc, normalize_unit(unit)) for desc, unit in description_units}
    filtered = []
    for row in rows:
        desc = row["normalized_description"]
        unit = normalize_unit(row["unit"])
        if row["signature"] in signature_set or desc in description_set or (desc, unit) in description_unit_set:
            row = dict(row)
            row["unit"] = unit
            _decode_memory_formula(row)
            filtered.append(row)
    return filtered


def list_quote_memory(limit: int = 200) -> List[Dict[str, Any]]:
    with connect() as conn:
        cid = company_id(conn)
        rows = conn.execute(
            """
            select qmi.normalized_description, pc.key as category, qmi.width_mm as width,
                   qmi.height_mm as height, qmi.diameter_mm as diameter, qmi.unit,
                   qmi.quote_unit_price, qmi.material_spec as quote_material_spec,
                   qmi.brand as quote_brand, qmi.note as quote_note,
                   '' as source_project, '' as source_customer,
                   qms.source_file_name as source_file, 1 as trained_count, qmi.created_at as updated_at
            from public.quote_memory_items qmi
            left join public.product_categories pc on pc.id = qmi.category_id
            join public.quote_memory_sources qms on qms.id = qmi.source_id
            where qmi.company_id = %s
            order by qmi.created_at desc
            limit %s
            """,
            (cid, limit),
        ).fetchall()
    decoded = []
    for row in rows:
        data = dict(row)
        _decode_memory_formula(data)
        decoded.append(data)
    return decoded


def _memory_note_with_formula(item: Dict[str, Any]) -> str:
    note = str(item.get("remark") or item.get("quote_note") or "")
    formula = {
        key: item.get(key)
        for key in [
            "quote_code",
            "quote_output_quantity",
            "quote_output_unit",
            "quote_output_unit_price",
            "reference_line_total",
            "formula_width",
            "formula_height",
            "formula_width2",
            "formula_height2",
            "formula_width3",
            "formula_height3",
            "formula_length",
            "formula_radius",
            "formula_angle",
            "formula_area",
            "formula_unit_price",
            "area_multiplier",
            "accessory_area",
            "accessory_unit_price",
            "product_code",
            "product_name_vi",
            "product_pricing_method",
            "product_required_fields",
            "matched_alias",
            "llm_enriched",
            "llm_confidence",
            "llm_reason",
        ]
        if item.get(key) not in (None, "")
    }
    if not formula:
        return note
    return f"{note} ||AI_FORMULA_JSON||{json.dumps(formula, ensure_ascii=False, separators=(',', ':'))}"


def _decode_memory_formula(row: Dict[str, Any]) -> None:
    note = str(row.get("quote_note") or "")
    marker = "||AI_FORMULA_JSON||"
    if marker not in note:
        return
    clean_note, payload = note.split(marker, 1)
    row["quote_note"] = clean_note.strip()
    try:
        formula = json.loads(payload)
    except json.JSONDecodeError:
        return
    if isinstance(formula, dict):
        row.update(formula)


def _category_id(conn, cid: str, category: Any):
    if not category:
        return None
    row = conn.execute(
        "select id from public.product_categories where company_id = %s and key = %s limit 1",
        (cid, str(category)),
    ).fetchone()
    return row["id"] if row else None


def _material_id(conn, cid: str, material: Any):
    if not material:
        return None
    row = conn.execute(
        "select id from public.materials where company_id = %s and code = %s limit 1",
        (cid, str(material)),
    ).fetchone()
    return row["id"] if row else None


def _price_source(item: Dict[str, Any]):
    source = str(item.get("reference_source") or "")
    if "AI quote memory" in source:
        return "completed_quote_memory"
    if source == "Nhập tay":
        return "manual"
    if item.get("quote_unit_price"):
        return "rule"
    return None


def _latest_session_id(conn, cid: str, project: str, customer: str):
    row = conn.execute(
        """
        select qs.id::text
        from public.quotation_sessions qs
        left join public.projects p on p.id = qs.project_id
        left join public.customers c on c.id = qs.customer_id
        where qs.company_id = %s
          and coalesce(p.name, '') = %s
          and coalesce(c.name, '') = %s
        order by qs.created_at desc
        limit 1
        """,
        (cid, project, customer),
    ).fetchone()
    return row["id"] if row else None
