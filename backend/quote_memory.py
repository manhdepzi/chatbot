from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List

from backend.database import get_db_connection
from backend.parser_engine import normalize_text
import backend.supabase_store as supabase_store


def _supabase_strict_enabled() -> bool:
    return getattr(supabase_store, "strict_enabled", lambda: True)()


def quote_memory_signature(item: Dict[str, Any]) -> str:
    normalized_description = quote_memory_description_key(item)
    parts = [
        normalized_description,
        normalize_text(item.get("category", "")),
        _dimension_key(item.get("width")),
        _dimension_key(item.get("height")),
        _dimension_key(item.get("diameter")),
        normalize_text(item.get("unit", "")),
    ]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def quote_memory_description_key(item: Dict[str, Any]) -> str:
    return normalize_text(item.get("description", "")).replace("lap dat ", "").strip()


def quote_memory_unit_key(item: Dict[str, Any]) -> str:
    return normalize_text(item.get("unit", ""))


def train_quote_memory(
    reference_items: Iterable[Dict[str, Any]],
    source_project: str = "",
    source_customer: str = "",
    source_file: str = "",
) -> int:
    if supabase_store.enabled():
        try:
            return supabase_store.train_quote_memory(
                reference_items,
                quote_memory_signature,
                quote_memory_description_key,
                source_project=source_project,
                source_customer=source_customer,
                source_file=source_file,
            )
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase train_quote_memory unavailable; using local dev DB because USE_SUPABASE_DB=0: {exc}")
    conn = get_db_connection()
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

        base_signature = quote_memory_signature(item)
        signature_occurrences[base_signature] = signature_occurrences.get(base_signature, 0) + 1
        occurrence = signature_occurrences[base_signature]
        signature = base_signature if occurrence == 1 else hashlib.sha1(
            f"{base_signature}|occurrence|{occurrence}".encode("utf-8")
        ).hexdigest()
        normalized_description = quote_memory_description_key(item)
        conn.execute(
            """
            INSERT INTO quote_memory
            (signature, normalized_description, category, width, height, diameter, length, unit,
             quote_unit_price, quote_material_spec, quote_brand, quote_note,
             source_project, source_customer, source_file, trained_count,
             quote_code, quote_output_quantity, quote_output_unit, quote_output_unit_price,
             reference_line_total, formula_width, formula_height, formula_width2,
             formula_height2, formula_length, formula_radius, formula_angle, formula_area,
             formula_unit_price, area_multiplier, accessory_area, accessory_unit_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signature) DO UPDATE SET
                quote_unit_price = excluded.quote_unit_price,
                quote_material_spec = excluded.quote_material_spec,
                quote_brand = excluded.quote_brand,
                quote_note = excluded.quote_note,
                quote_code = excluded.quote_code,
                quote_output_quantity = excluded.quote_output_quantity,
                quote_output_unit = excluded.quote_output_unit,
                quote_output_unit_price = excluded.quote_output_unit_price,
                reference_line_total = excluded.reference_line_total,
                formula_width = excluded.formula_width,
                formula_height = excluded.formula_height,
                formula_width2 = excluded.formula_width2,
                formula_height2 = excluded.formula_height2,
                formula_length = excluded.formula_length,
                formula_radius = excluded.formula_radius,
                formula_angle = excluded.formula_angle,
                formula_area = excluded.formula_area,
                formula_unit_price = excluded.formula_unit_price,
                area_multiplier = excluded.area_multiplier,
                accessory_area = excluded.accessory_area,
                accessory_unit_price = excluded.accessory_unit_price,
                source_project = excluded.source_project,
                source_customer = excluded.source_customer,
                source_file = excluded.source_file,
                trained_count = quote_memory.trained_count + 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                signature,
                normalized_description,
                item.get("category"),
                item.get("width"),
                item.get("height"),
                item.get("diameter"),
                item.get("length"),
                item.get("unit"),
                unit_price,
                item.get("quote_material_spec"),
                item.get("quote_brand"),
                item.get("remark") or item.get("quote_note"),
                source_project,
                source_customer,
                source_file,
                item.get("quote_code"),
                item.get("quote_output_quantity") or item.get("quantity"),
                item.get("quote_output_unit") or item.get("unit"),
                item.get("quote_output_unit_price") or unit_price,
                item.get("reference_line_total"),
                item.get("formula_width") or item.get("width"),
                item.get("formula_height") or item.get("height"),
                item.get("formula_width2"),
                item.get("formula_height2"),
                item.get("formula_length") or item.get("length"),
                item.get("formula_radius"),
                item.get("formula_angle"),
                item.get("formula_area"),
                item.get("formula_unit_price"),
                item.get("area_multiplier"),
                item.get("accessory_area"),
                item.get("accessory_unit_price"),
            ),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def quote_memory_lookup(items: Iterable[Dict[str, Any]], source_file: str = "") -> Dict[str, Dict[str, Any]]:
    item_list = list(items)
    signatures = sorted({quote_memory_signature(item) for item in item_list})
    description_units = sorted({
        (quote_memory_description_key(item), quote_memory_unit_key(item))
        for item in item_list
        if quote_memory_description_key(item)
    })
    descriptions_set = {quote_memory_description_key(item) for item in item_list if quote_memory_description_key(item)}
    has_composite_description = False
    for description in list(descriptions_set):
        if "+" not in description:
            continue
        has_composite_description = True
        descriptions_set.update(part.strip() for part in description.split("+") if part.strip())
    descriptions = sorted(descriptions_set)
    if not signatures and not description_units and not descriptions:
        return {}

    rows = []
    if supabase_store.enabled():
        try:
            rows = supabase_store.quote_memory_rows(signatures, description_units, descriptions, source_file)
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase quote_memory_lookup unavailable; using local dev DB because USE_SUPABASE_DB=0: {exc}")
            rows = []
    if not rows and not supabase_store.enabled():
        conn = get_db_connection()
        source_filter = " AND source_file = ?" if source_file else ""
        source_params = [source_file] if source_file else []

        if signatures:
            placeholders = ",".join("?" for _ in signatures)
            rows.extend(conn.execute(
                f"""
                SELECT rowid AS memory_id, signature, normalized_description, unit, quote_unit_price, quote_material_spec, quote_brand, quote_note,
                       source_project, source_customer, source_file, trained_count, updated_at,
                       quote_code, quote_output_quantity, quote_output_unit, quote_output_unit_price,
                       reference_line_total, formula_width, formula_height, formula_width2,
                       formula_height2, formula_length, formula_radius, formula_angle, formula_area,
                       formula_unit_price, area_multiplier, accessory_area, accessory_unit_price
                FROM quote_memory
                WHERE signature IN ({placeholders}){source_filter}
                """,
                signatures + source_params,
            ).fetchall())
        if description_units:
            clauses = " OR ".join("(normalized_description = ? AND unit = ?)" for _ in description_units)
            params = [value for pair in description_units for value in pair]
            rows.extend(conn.execute(
                f"""
                SELECT rowid AS memory_id, signature, normalized_description, unit, quote_unit_price, quote_material_spec, quote_brand, quote_note,
                       source_project, source_customer, source_file, trained_count, updated_at,
                       quote_code, quote_output_quantity, quote_output_unit, quote_output_unit_price,
                       reference_line_total, formula_width, formula_height, formula_width2,
                       formula_height2, formula_length, formula_radius, formula_angle, formula_area,
                       formula_unit_price, area_multiplier, accessory_area, accessory_unit_price
                FROM quote_memory
                WHERE ({clauses}){source_filter}
                """,
                params + source_params,
            ).fetchall())
        if descriptions:
            placeholders = ",".join("?" for _ in descriptions)
            rows.extend(conn.execute(
                f"""
                SELECT rowid AS memory_id, signature, normalized_description, unit, quote_unit_price, quote_material_spec, quote_brand, quote_note,
                       source_project, source_customer, source_file, trained_count, updated_at,
                       quote_code, quote_output_quantity, quote_output_unit, quote_output_unit_price,
                       reference_line_total, formula_width, formula_height, formula_width2,
                       formula_height2, formula_length, formula_radius, formula_angle, formula_area,
                       formula_unit_price, area_multiplier, accessory_area, accessory_unit_price
                FROM quote_memory
                WHERE normalized_description IN ({placeholders}){source_filter}
                """,
                descriptions + source_params,
            ).fetchall())
        if has_composite_description:
            rows.extend(conn.execute(
                f"""
                SELECT rowid AS memory_id, signature, normalized_description, unit, quote_unit_price, quote_material_spec, quote_brand, quote_note,
                       source_project, source_customer, source_file, trained_count, updated_at,
                       quote_code, quote_output_quantity, quote_output_unit, quote_output_unit_price,
                       reference_line_total, formula_width, formula_height, formula_width2,
                       formula_height2, formula_length, formula_radius, formula_angle, formula_area,
                       formula_unit_price, area_multiplier, accessory_area, accessory_unit_price
                FROM quote_memory
                WHERE 1 = 1{source_filter}
                """,
                source_params,
            ).fetchall())
        conn.close()
    lookup: Dict[str, Dict[str, Any]] = {}
    lookup["__rows__"] = []
    lookup["__queues__"] = {}
    seen_signatures = set()
    unique_rows = []
    for row in rows:
        data = dict(row)
        signature = data.get("signature")
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        unique_rows.append(data)

    unique_rows.sort(key=_memory_sort_key)
    for data in unique_rows:
        lookup["__rows__"].append(data)
        lookup[data["signature"]] = data
        description_key = _description_lookup_key(data["normalized_description"], data["unit"])
        description_only_key = _description_only_lookup_key(data["normalized_description"])
        lookup["__queues__"].setdefault(description_key, []).append(data)
        lookup["__queues__"].setdefault(description_only_key, []).append(data)
        if description_key not in lookup:
            lookup[description_key] = data
        else:
            lookup[description_key] = {"ambiguous": True}
        description_only_key = _description_only_lookup_key(data["normalized_description"])
        if description_only_key not in lookup:
            lookup[description_only_key] = data
        else:
            lookup[description_only_key] = {"ambiguous": True}
    return lookup


def quote_memory_item_description_lookup_key(item: Dict[str, Any]) -> str:
    return _description_lookup_key(quote_memory_description_key(item), quote_memory_unit_key(item))


def quote_memory_item_description_only_lookup_key(item: Dict[str, Any]) -> str:
    return _description_only_lookup_key(quote_memory_description_key(item))


def _description_lookup_key(normalized_description: str, unit: str) -> str:
    return f"description::{normalized_description}::{normalize_text(unit)}"


def _description_only_lookup_key(normalized_description: str) -> str:
    return f"description::{normalized_description}"


def list_quote_memory(limit: int = 200) -> List[Dict[str, Any]]:
    if supabase_store.enabled():
        try:
            return supabase_store.list_quote_memory(limit)
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase list_quote_memory unavailable; using local dev DB because USE_SUPABASE_DB=0: {exc}")
    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT normalized_description, category, width, height, diameter, unit,
               quote_unit_price, quote_material_spec, quote_brand, quote_note,
               source_project, source_customer, source_file, trained_count, updated_at,
               quote_code, quote_output_quantity, quote_output_unit, quote_output_unit_price,
               reference_line_total, formula_area, formula_unit_price, area_multiplier,
               accessory_area, accessory_unit_price
        FROM quote_memory
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def quote_memory_candidate_rows(limit: int = 2000, source_file: str = "") -> List[Dict[str, Any]]:
    if supabase_store.enabled():
        try:
            rows = supabase_store.list_quote_memory(limit)
            if source_file:
                rows = [row for row in rows if row.get("source_file") == source_file]
            return rows
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase quote_memory_candidate_rows unavailable; using local dev DB because USE_SUPABASE_DB=0: {exc}")
    conn = get_db_connection()
    source_clause = "WHERE source_file = ?" if source_file else ""
    params = (source_file, limit) if source_file else (limit,)
    rows = conn.execute(
        f"""
        SELECT signature, normalized_description, category, width, height, diameter, length, unit,
               quote_unit_price, quote_material_spec, quote_brand, quote_note,
               source_project, source_customer, source_file, trained_count, updated_at,
               quote_code, quote_output_quantity, quote_output_unit, quote_output_unit_price,
               reference_line_total, formula_width, formula_height, formula_width2,
               formula_height2, formula_length, formula_radius, formula_angle, formula_area,
               formula_unit_price, area_multiplier, accessory_area, accessory_unit_price
        FROM quote_memory
        {source_clause}
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _dimension_key(value: Any) -> str:
    try:
        return str(round(float(value or 0), 2))
    except (TypeError, ValueError):
        return "0"


def _memory_sort_key(data: Dict[str, Any]) -> tuple[int, str]:
    value = data.get("memory_id") or 0
    try:
        return (0, f"{int(value):020d}")
    except (TypeError, ValueError):
        return (1, str(value))

