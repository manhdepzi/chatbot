from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional

from backend.database import get_db_connection
import backend.supabase_store as supabase_store

_ALIAS_ROWS_CACHE: Dict[str, Any] = {"expires_at": 0.0, "rows": []}


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("đ", "d").replace("Đ", "D")
    text = re.sub(r"[\+\-/_,;:()\[\]{}|]+", " ", text)
    return re.sub(r"\s+", " ", text.lower()).strip()


MASTER_PRODUCTS = [
    {
        "product_code": "RECT_DUCT",
        "display_name_vi": "Ống gió vuông/chữ nhật",
        "category": "SUPPLY_DUCT",
        "pricing_method": "rule_area",
        "required_fields": "width,height,length,thickness,material,quantity",
        "default_unit": "m",
    },
    {
        "product_code": "RECT_ELBOW",
        "display_name_vi": "Cút/co vuông",
        "category": "ELBOW",
        "pricing_method": "rule_area",
        "required_fields": "width,height,thickness,material,quantity",
        "default_unit": "cai",
    },
    {
        "product_code": "REDUCER",
        "display_name_vi": "Côn thu/giảm cấp",
        "category": "REDUCER",
        "pricing_method": "rule_area",
        "required_fields": "width,height,width2,height2,length,thickness,material,quantity",
        "default_unit": "cai",
    },
    {
        "product_code": "TEE_BRANCH",
        "display_name_vi": "Tê/chạc",
        "category": "TEE",
        "pricing_method": "rule_area",
        "required_fields": "width,height,thickness,material,quantity",
        "default_unit": "cai",
    },
    {
        "product_code": "LOUVER",
        "display_name_vi": "Cửa gió Louver",
        "category": "LOUVER",
        "pricing_method": "rule_or_price_list",
        "required_fields": "width,height,quantity",
        "default_unit": "cai",
    },
    {
        "product_code": "LOUVER_WITH_INSECT_SCREEN",
        "display_name_vi": "Cửa gió Louver kèm lưới chắn côn trùng",
        "category": "LOUVER",
        "pricing_method": "composite_rule",
        "required_fields": "width,height,quantity",
        "default_unit": "cai",
    },
    {
        "product_code": "GRILLE_WITH_OBD",
        "display_name_vi": "Cửa/miệng gió kèm van OBD",
        "category": "SQUARE_DIFFUSER",
        "pricing_method": "composite_rule",
        "required_fields": "width,height,quantity",
        "default_unit": "cai",
    },
    {
        "product_code": "OBD_DAMPER",
        "display_name_vi": "Van OBD/van điều chỉnh lưu lượng",
        "category": "VOLUME_CONTROL_DAMPER",
        "pricing_method": "rule_or_price_list",
        "required_fields": "width,height,quantity",
        "default_unit": "cai",
    },
    {
        "product_code": "MANUAL_VOLUME_DAMPER",
        "display_name_vi": "Van gió điều chỉnh bằng tay",
        "category": "VOLUME_CONTROL_DAMPER",
        "pricing_method": "rule_or_price_list",
        "required_fields": "width,height,quantity",
        "default_unit": "cai",
    },
    {
        "product_code": "MFD_L250",
        "display_name_vi": "Van MFD L250",
        "category": "MOTORIZED_DAMPER",
        "pricing_method": "rule_piece_area",
        "required_fields": "width,height,quantity",
        "default_unit": "cai",
    },
    {
        "product_code": "FD_L250",
        "display_name_vi": "Van FD L250",
        "category": "FIRE_DAMPER",
        "pricing_method": "rule_piece_area",
        "required_fields": "width,height,quantity",
        "default_unit": "cai",
    },
    {
        "product_code": "HANGER_ACCESSORY",
        "display_name_vi": "Ty ren/phụ kiện treo",
        "category": "ACCESSORY",
        "pricing_method": "manual_or_price_list",
        "required_fields": "quantity",
        "default_unit": "cai",
    },
]


ALIASES = [
    ("RECT_DUCT", "ống gió vuông", "vi", 100),
    ("RECT_DUCT", "ống cn", "vi", 95),
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


def ensure_product_catalog_schema(conn=None) -> None:
    own_conn = conn is None
    conn = conn or get_db_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS product_master (
            product_code TEXT PRIMARY KEY,
            display_name_vi TEXT NOT NULL,
            category TEXT NOT NULL,
            pricing_method TEXT NOT NULL,
            required_fields TEXT NOT NULL DEFAULT '',
            default_unit TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS product_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alias_text TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            product_code TEXT NOT NULL,
            language TEXT,
            confidence REAL NOT NULL DEFAULT 100,
            status TEXT NOT NULL DEFAULT 'approved',
            source TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(normalized_alias, product_code)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_product_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            line_signature TEXT NOT NULL,
            description TEXT NOT NULL,
            product_code TEXT,
            category TEXT,
            unit_price REAL,
            unit TEXT,
            note TEXT,
            approved_by TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    if own_conn:
        conn.close()


def seed_product_catalog(conn=None) -> None:
    if supabase_store.enabled() and conn is None:
        supabase_store.ensure_seed_data()
        return
    own_conn = conn is None
    conn = conn or get_db_connection()
    ensure_product_catalog_schema(conn)
    for product in MASTER_PRODUCTS:
        conn.execute(
            """
            INSERT INTO product_master
            (product_code, display_name_vi, category, pricing_method, required_fields, default_unit, active)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(product_code) DO UPDATE SET
                display_name_vi = excluded.display_name_vi,
                category = excluded.category,
                pricing_method = excluded.pricing_method,
                required_fields = excluded.required_fields,
                default_unit = excluded.default_unit,
                active = 1
            """,
            (
                product["product_code"],
                product["display_name_vi"],
                product["category"],
                product["pricing_method"],
                product["required_fields"],
                product["default_unit"],
            ),
        )
    for product_code, alias, language, confidence in ALIASES:
        upsert_product_alias(product_code, alias, language, confidence, "seed", conn=conn)
    conn.commit()
    if own_conn:
        conn.close()


def upsert_product_alias(
    product_code: str,
    alias_text: str,
    language: str = "",
    confidence: float = 100,
    source: str = "manual",
    conn=None,
) -> None:
    if not product_code or not alias_text:
        return
    if supabase_store.enabled() and conn is None:
        supabase_store.upsert_product_alias(product_code, alias_text, language, confidence, source)
        return
    own_conn = conn is None
    conn = conn or get_db_connection()
    ensure_product_catalog_schema(conn)
    conn.execute(
        """
        INSERT INTO product_aliases
        (alias_text, normalized_alias, product_code, language, confidence, status, source)
        VALUES (?, ?, ?, ?, ?, 'approved', ?)
        ON CONFLICT(normalized_alias, product_code) DO UPDATE SET
            alias_text = excluded.alias_text,
            language = excluded.language,
            confidence = max(product_aliases.confidence, excluded.confidence),
            status = 'approved',
            source = excluded.source
        """,
        (alias_text, normalize_text(alias_text), product_code, language, float(confidence), source),
    )
    conn.commit()
    if own_conn:
        conn.close()


def list_product_master() -> List[Dict[str, Any]]:
    if supabase_store.enabled():
        return supabase_store.list_product_master()
    conn = get_db_connection()
    ensure_product_catalog_schema(conn)
    rows = conn.execute(
        """
        SELECT product_code, display_name_vi, category, pricing_method, required_fields, default_unit, active
        FROM product_master
        ORDER BY product_code
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_product_aliases(limit: int = 500) -> List[Dict[str, Any]]:
    if supabase_store.enabled():
        try:
            return supabase_store.list_product_aliases(limit)
        except Exception as exc:
            if getattr(supabase_store, "strict_enabled", lambda: True)():
                print(f"Supabase list_product_aliases unavailable, using local fallback: {exc}")
    conn = get_db_connection()
    ensure_product_catalog_schema(conn)
    rows = conn.execute(
        """
        SELECT pa.alias_text, pa.product_code, pm.display_name_vi, pm.category,
               pa.language, pa.confidence, pa.status, pa.source, pa.created_at
        FROM product_aliases pa
        LEFT JOIN product_master pm ON pm.product_code = pa.product_code
        ORDER BY pa.created_at DESC, pa.confidence DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def match_product(text: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_text(text)
    if not normalized:
        return None
    rows = _product_alias_match_rows_cached()

    best: Optional[Dict[str, Any]] = None
    for row in rows:
        alias = row["normalized_alias"]
        if not alias:
            continue
        if _alias_in_text(normalized, alias):
            score = float(row["confidence"] or 0) + min(len(alias), 80) / 100.0
            if best is None or score > best["match_score"]:
                best = dict(row)
                best["match_score"] = score
                best["matched_alias"] = row["alias_text"]
    if best:
        best["required_fields"] = _required_fields(best.get("required_fields"))
    return best


def _product_alias_match_rows_cached(ttl_seconds: int = 300) -> List[Dict[str, Any]]:
    now = time.time()
    if _ALIAS_ROWS_CACHE["rows"] and now < float(_ALIAS_ROWS_CACHE["expires_at"] or 0):
        return list(_ALIAS_ROWS_CACHE["rows"])

    rows: List[Dict[str, Any]]
    if supabase_store.enabled():
        try:
            rows = supabase_store.product_alias_match_rows()
        except Exception as exc:
            print(f"Supabase product_alias_match_rows unavailable, using local fallback: {exc}")
            rows = _local_product_alias_match_rows()
    else:
        rows = _local_product_alias_match_rows()

    _ALIAS_ROWS_CACHE["rows"] = [dict(row) for row in rows]
    _ALIAS_ROWS_CACHE["expires_at"] = now + ttl_seconds
    return list(_ALIAS_ROWS_CACHE["rows"])


def _local_product_alias_match_rows() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    ensure_product_catalog_schema(conn)
    rows = conn.execute(
        """
        SELECT pa.alias_text, pa.normalized_alias, pa.product_code, pa.confidence,
               pm.display_name_vi, pm.category, pm.pricing_method, pm.required_fields, pm.default_unit
        FROM product_aliases pa
        JOIN product_master pm ON pm.product_code = pa.product_code
        WHERE pa.status = 'approved' AND pm.active = 1
        """
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def line_signature(item: Dict[str, Any]) -> str:
    raw = "|".join(
        str(item.get(key, "") or "")
        for key in ["description", "width", "height", "diameter", "length", "quantity", "unit"]
    )
    return hashlib.sha1(normalize_text(raw).encode("utf-8")).hexdigest()


def save_sales_product_answer(
    item: Dict[str, Any],
    product_code: str = "",
    category: str = "",
    unit_price: float = 0.0,
    unit: str = "",
    note: str = "",
    approved_by: str = "",
    save_alias: bool = False,
) -> None:
    if supabase_store.enabled():
        supabase_store.save_sales_product_answer(
            item,
            product_code=product_code,
            category=category,
            unit_price=unit_price,
            unit=unit,
            note=note,
            approved_by=approved_by,
            save_alias=save_alias,
            signature=line_signature(item),
        )
        return
    conn = get_db_connection()
    ensure_product_catalog_schema(conn)
    conn.execute(
        """
        INSERT INTO sales_product_answers
        (line_signature, description, product_code, category, unit_price, unit, note, approved_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            line_signature(item),
            str(item.get("description") or ""),
            product_code,
            category,
            float(unit_price or 0),
            unit or item.get("unit") or "",
            note,
            approved_by,
        ),
    )
    if save_alias and product_code:
        upsert_product_alias(product_code, str(item.get("description") or ""), "auto", 92, "sales_confirmed", conn=conn)
    conn.commit()
    conn.close()


def list_sales_answers(limit: int = 200) -> List[Dict[str, Any]]:
    if supabase_store.enabled():
        try:
            return supabase_store.list_sales_answers(limit)
        except Exception as exc:
            if getattr(supabase_store, "strict_enabled", lambda: True)():
                print(f"Supabase list_sales_answers unavailable, using local fallback: {exc}")
    conn = get_db_connection()
    ensure_product_catalog_schema(conn)
    rows = conn.execute(
        """
        SELECT description, product_code, category, unit_price, unit, note, approved_by, created_at
        FROM sales_product_answers
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _alias_in_text(text: str, alias: str) -> bool:
    if not alias:
        return False
    if " " not in alias and alias.isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None
    return alias in text


def _required_fields(value: Any) -> List[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]
