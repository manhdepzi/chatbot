from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Tuple

from dotenv import load_dotenv

from backend import llm_provider
from backend.parser_engine import extract_dimensions_from_text, infer_material, normalize_text, parse_number
from backend.product_catalog import list_product_master, match_product


load_dotenv()


def enabled() -> bool:
    return llm_provider.enabled()


def _json_from_text(text: str) -> Any:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _safe_float(value: Any):
    parsed = parse_number(value)
    return parsed if parsed is not None else None


def _missing_required_fields(item: Dict[str, Any]) -> List[str]:
    product_match = match_product(item.get("description", ""))
    required = product_match.get("required_fields", []) if product_match else []
    missing = []
    for field in required:
        if field == "material":
            if not item.get("material"):
                missing.append(field)
            continue
        if item.get(field) in (None, "", 0):
            missing.append(field)
    return missing


def item_needs_llm_enrichment(item: Dict[str, Any]) -> bool:
    if item.get("llm_enriched"):
        return False
    if item.get("category") == "UNKNOWN":
        return True
    if not item.get("product_code") and match_product(item.get("description", "")):
        return True
    if _missing_required_fields(item):
        return True
    formula_fields = [
        "quote_code",
        "formula_width",
        "formula_height",
        "formula_width2",
        "formula_height2",
        "formula_length",
        "formula_area",
        "formula_unit_price",
        "area_multiplier",
        "accessory_area",
        "accessory_unit_price",
    ]
    return not any(item.get(field) not in (None, "", 0) for field in formula_fields)


def _apply_llm_result(item: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(item)
    warnings = list(updated.get("warnings") or [])

    product_code = str(result.get("product_code") or "").strip()
    if product_code:
        updated["product_code"] = product_code
        product_match = next(
            (row for row in list_product_master() if row.get("product_code") == product_code),
            None,
        )
        if product_match:
            updated["product_name_vi"] = product_match.get("display_name_vi")
            updated["category"] = product_match.get("category") or updated.get("category")
            updated["product_pricing_method"] = product_match.get("pricing_method")
            updated["product_required_fields"] = [
                part.strip()
                for part in str(product_match.get("required_fields") or "").split(",")
                if part.strip()
            ]

    for key in ["category", "quote_code", "quote_material_spec", "quote_brand", "quote_note", "unit"]:
        if result.get(key) not in (None, ""):
            updated[key] = result.get(key)

    dims = result.get("dimensions") if isinstance(result.get("dimensions"), dict) else {}
    for key in ["width", "height", "diameter", "length", "thickness"]:
        value = _safe_float(dims.get(key) if key in dims else result.get(key))
        if value is not None and updated.get(key) in (None, "", 0):
            updated[key] = value

    formula = result.get("formula") if isinstance(result.get("formula"), dict) else {}
    formula_map = {
        "formula_width": ["width", "w", "formula_width"],
        "formula_height": ["height", "h", "formula_height"],
        "formula_width2": ["width2", "w2", "formula_width2"],
        "formula_height2": ["height2", "h2", "formula_height2"],
        "formula_length": ["length", "l", "formula_length"],
        "formula_radius": ["radius", "r", "formula_radius"],
        "formula_angle": ["angle", "formula_angle"],
        "formula_area": ["area", "formula_area"],
        "formula_unit_price": ["unit_price", "formula_unit_price"],
        "area_multiplier": ["area_multiplier", "multiplier", "factor"],
        "accessory_area": ["accessory_area"],
        "accessory_unit_price": ["accessory_unit_price"],
    }
    for target, aliases in formula_map.items():
        for alias in aliases:
            value = _safe_float(formula.get(alias))
            if value is not None:
                updated[target] = value
                break

    if not updated.get("material"):
        updated["material"] = infer_material(
            " ".join(str(part or "") for part in [updated.get("description"), updated.get("quote_material_spec")]),
            "GI",
        )

    confidence = _safe_float(result.get("confidence"))
    updated["llm_enriched"] = True
    updated["llm_confidence"] = confidence if confidence is not None else 0
    updated["llm_reason"] = str(result.get("reason") or "").strip()

    if confidence is None or confidence < 85:
        warnings.append("LLM đã suy luận dòng này nhưng độ tin cậy chưa đủ cao; sales/QS cần duyệt trước khi dùng tự động.")
        updated["can_auto_quote"] = False
        updated["pricing_confidence"] = "needs_confirmation"
    if not product_code:
        warnings.append("LLM chưa xác định được mã sản phẩm chuẩn.")
    updated["warnings"] = list(dict.fromkeys(warnings))
    return updated


def enrich_reference_items_with_llm(
    items: Iterable[Dict[str, Any]],
    max_items: int = 80,
    batch_size: int = 12,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    item_list = [dict(item) for item in items]
    if not enabled():
        status = llm_provider.status()
        return item_list, {
            "enabled": False,
            "processed": 0,
            "updated": 0,
            "warnings": [f"Chưa cấu hình API key cho LLM_PROVIDER={status['provider']}."],
        }

    target_indexes = [idx for idx, item in enumerate(item_list) if item_needs_llm_enrichment(item)]
    target_indexes = target_indexes[: max(0, max_items)]
    if not target_indexes:
        return item_list, {"enabled": True, "processed": 0, "updated": 0, "warnings": []}

    products = [
        {
            "product_code": row.get("product_code"),
            "name": row.get("display_name_vi"),
            "category": row.get("category"),
            "pricing_method": row.get("pricing_method"),
            "required_fields": row.get("required_fields"),
        }
        for row in list_product_master()
    ]
    warnings: List[str] = []
    updated_count = 0

    for start in range(0, len(target_indexes), batch_size):
        batch_indexes = target_indexes[start : start + batch_size]
        batch = []
        for local_id, idx in enumerate(batch_indexes, start=1):
            item = item_list[idx]
            text_dims = extract_dimensions_from_text(str(item.get("description") or ""))
            batch.append(
                {
                    "id": local_id,
                    "description": item.get("description"),
                    "category": item.get("category"),
                    "unit": item.get("unit"),
                    "quantity": item.get("quantity"),
                    "unit_price": item.get("reference_unit_price") or item.get("quote_unit_price"),
                    "line_total": item.get("reference_line_total"),
                    "material_spec": item.get("quote_material_spec"),
                    "remark": item.get("remark"),
                    "text_dimensions": text_dims,
                }
            )

        prompt = f"""
Bạn là QS HVAC của Kaiyo. Hãy chuẩn hóa các dòng báo giá đã hoàn thành.

Không được tự bịa giá. Giá đã có trong input chỉ được giữ lại/giải thích nguồn.
Nhiệm vụ:
1. Map từng dòng về product_code chuẩn nếu đủ chắc.
2. Chuẩn hóa category.
3. Trích W/H/D/L/độ dày/vật liệu nếu thấy trong mô tả.
4. Ghi lại công thức/đại lượng tính nếu suy ra được từ dòng báo giá.
5. Nếu không chắc, đặt confidence < 85 và nêu reason.

Danh mục sản phẩm chuẩn:
{json.dumps(products, ensure_ascii=False)}

Các dòng cần xử lý:
{json.dumps(batch, ensure_ascii=False)}

Trả về JSON array, mỗi phần tử:
{{
  "id": 1,
  "product_code": "RECT_DUCT|RECT_ELBOW|REDUCER|TEE_BRANCH|LOUVER|LOUVER_WITH_INSECT_SCREEN|GRILLE_WITH_OBD|OBD_DAMPER|MANUAL_VOLUME_DAMPER|MFD_L250|FD_L250|HANGER_ACCESSORY hoặc rỗng",
  "category": "category_key hoặc UNKNOWN",
  "dimensions": {{"width": null, "height": null, "diameter": null, "length": null, "thickness": null}},
  "material": "GI|SS|MS|PU hoặc rỗng",
  "quote_code": "mã báo giá nếu có",
  "quote_material_spec": "vật liệu chế tạo nếu có",
  "quote_brand": "hãng/xuất xứ nếu có",
  "formula": {{
    "width": null,
    "height": null,
    "width2": null,
    "height2": null,
    "length": null,
    "area": null,
    "unit_price": null,
    "area_multiplier": null,
    "accessory_area": null,
    "accessory_unit_price": null
  }},
  "confidence": 0,
  "reason": "giải thích ngắn bằng tiếng Việt"
}}
"""
        try:
            parsed = llm_provider.generate_json(prompt, temperature=0.1)
            if isinstance(parsed, dict):
                parsed = parsed.get("items") or []
            result_by_id = {int(row.get("id")): row for row in parsed if isinstance(row, dict) and row.get("id")}
            for local_id, idx in enumerate(batch_indexes, start=1):
                result = result_by_id.get(local_id)
                if not result:
                    item_list[idx].setdefault("warnings", []).append("LLM không trả kết quả cho dòng này.")
                    continue
                item_list[idx] = _apply_llm_result(item_list[idx], result)
                updated_count += 1
        except Exception as exc:
            warnings.append(f"LLM lỗi ở batch {start // batch_size + 1}: {type(exc).__name__}: {exc}")

    return item_list, {
        "enabled": True,
        "processed": len(target_indexes),
        "updated": updated_count,
        "warnings": warnings,
    }
