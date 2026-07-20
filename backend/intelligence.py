from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

from backend.database import get_db_connection
import backend.supabase_store as supabase_store


def _supabase_strict_enabled() -> bool:
    return getattr(supabase_store, "strict_enabled", lambda: True)()


def item_signature(item: Dict[str, Any]) -> str:
    raw = "|".join(
        str(item.get(key, "") or "").strip().lower()
        for key in ["category", "description", "material", "thickness", "width", "height", "diameter", "unit"]
    )
    raw = re.sub(r"\s+", " ", raw)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def list_price_lists() -> List[Dict[str, Any]]:
    if supabase_store.enabled():
        try:
            return supabase_store.list_price_lists()
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase list_price_lists fallback to SQLite: {exc}")
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT id, name, supplier, region, customer, valid_from, valid_to, notes, is_default
        FROM price_lists
        ORDER BY is_default DESC, name
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_company_settings() -> Dict[str, str]:
    if supabase_store.enabled():
        try:
            return supabase_store.get_company_settings()
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase get_company_settings fallback to SQLite: {exc}")
    conn = get_db_connection()
    rows = conn.execute("SELECT key, value FROM company_settings").fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def update_company_setting(key: str, value: str, description: str = "") -> None:
    if supabase_store.enabled():
        try:
            supabase_store.update_company_setting(key, value, description)
            return
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase update_company_setting fallback to SQLite: {exc}")
    conn = get_db_connection()
    conn.execute("""
        INSERT INTO company_settings (key, value, description)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                      description = COALESCE(NULLIF(excluded.description, ''), company_settings.description)
    """, (key, value, description))
    conn.commit()
    conn.close()


def list_company_settings() -> List[Dict[str, Any]]:
    if supabase_store.enabled():
        try:
            return supabase_store.list_company_settings()
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase list_company_settings fallback to SQLite: {exc}")
    conn = get_db_connection()
    rows = conn.execute("SELECT key, value, description FROM company_settings ORDER BY key").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def create_price_list(name: str, supplier: str = "", region: str = "", customer: str = "", notes: str = "") -> int:
    if supabase_store.enabled():
        try:
            return supabase_store.create_price_list(name, supplier, region, customer, notes)
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase create_price_list fallback to SQLite: {exc}")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO price_lists (name, supplier, region, customer, notes, is_default)
        VALUES (?, ?, ?, ?, ?, 0)
    """, (name, supplier, region, customer, notes))
    if cursor.lastrowid:
        price_list_id = cursor.lastrowid
    else:
        price_list_id = cursor.execute("SELECT id FROM price_lists WHERE name = ?", (name,)).fetchone()["id"]
    conn.commit()
    conn.close()
    return int(price_list_id)


def get_price_rules_for_list(price_list_id: Optional[int]) -> Dict[Tuple[str, float], float]:
    if supabase_store.enabled():
        try:
            return supabase_store.get_price_rules_for_list(str(price_list_id) if price_list_id else None)
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase get_price_rules_for_list fallback to SQLite: {exc}")
    conn = get_db_connection()
    if price_list_id:
        rows = conn.execute("""
            SELECT material, thickness, unit_price
            FROM price_list_items
            WHERE price_list_id = ?
        """, (price_list_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT material, thickness, unit_price
            FROM pricing_rules
        """).fetchall()
    conn.close()
    return {(row["material"], float(row["thickness"])): float(row["unit_price"]) for row in rows}


def upsert_price_list_item(
    price_list_id: int,
    material: str,
    thickness: float,
    unit_price: float,
    unit: str = "m2",
    source: str = "User",
) -> None:
    if supabase_store.enabled():
        try:
            supabase_store.upsert_price_list_item(str(price_list_id), material, thickness, unit_price, unit, source)
            return
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase upsert_price_list_item fallback to SQLite: {exc}")
    conn = get_db_connection()
    conn.execute("""
        INSERT INTO price_list_items (price_list_id, material, thickness, unit_price, unit, source)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(price_list_id, material, thickness, unit)
        DO UPDATE SET unit_price = excluded.unit_price, source = excluded.source
    """, (price_list_id, material, thickness, unit_price, unit, source))
    conn.commit()
    conn.close()


def learn_from_quote(
    items: List[Dict[str, Any]],
    coefficients: Dict[str, float],
    price_overrides: Dict[Tuple[str, float], float],
    source_project: str = "",
    source_customer: str = "",
) -> int | str:
    if supabase_store.enabled():
        try:
            return supabase_store.train_quote_memory(
                items,
                item_signature,
                lambda item: re.sub(r"\s+", " ", str(item.get("description") or "").strip().lower()),
                source_project=source_project,
                source_customer=source_customer,
                source_file="AI current quotation",
            )
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase learn_from_quote fallback to SQLite: {exc}")
    conn = get_db_connection()
    count = 0
    for item in items:
        unit_price = None
        key = (item.get("material", "GI"), float(item.get("thickness") or 0))
        if key in price_overrides:
            unit_price = float(price_overrides[key])
        elif item.get("calculated_area") and item.get("material_cost"):
            unit_price = float(item["material_cost"]) / max(float(item["calculated_area"]), 0.0001)

        conn.execute("""
            INSERT INTO learned_pricing
            (signature, category, material, thickness, unit, unit_price, coefficients_json, source_project, source_customer)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item_signature(item),
            item.get("category"),
            item.get("material"),
            item.get("thickness"),
            item.get("unit"),
            unit_price,
            json.dumps(coefficients, ensure_ascii=False),
            source_project,
            source_customer,
        ))
        count += 1
    conn.commit()
    conn.close()
    return count


def learned_suggestions(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if supabase_store.enabled():
        try:
            rows = supabase_store.list_quote_memory(limit=2000)
            suggestions: Dict[str, Dict[str, Any]] = {}
            for item in items:
                sig = item_signature(item)
                desc = re.sub(r"\s+", " ", str(item.get("description") or "").strip().lower())
                for row in rows:
                    if row.get("normalized_description") == desc:
                        suggestions[sig] = row
                        break
            return suggestions
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase learned_suggestions fallback to SQLite: {exc}")
    conn = get_db_connection()
    suggestions: Dict[str, Dict[str, Any]] = {}
    for item in items:
        sig = item_signature(item)
        row = conn.execute("""
            SELECT unit_price, coefficients_json, source_project, source_customer, created_at
            FROM learned_pricing
            WHERE signature = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (sig,)).fetchone()
        if row:
            suggestions[sig] = dict(row)
            continue

        row = conn.execute("""
            SELECT unit_price, coefficients_json, source_project, source_customer, created_at
            FROM learned_pricing
            WHERE category = ? AND material = ? AND ABS(thickness - ?) < 0.001
            ORDER BY created_at DESC
            LIMIT 1
        """, (item.get("category"), item.get("material"), float(item.get("thickness") or 0))).fetchone()
        if row:
            suggestions[sig] = dict(row)
    conn.close()
    return suggestions


def save_quotation_session(
    name: str,
    items: List[Dict[str, Any]],
    summary: Dict[str, Any],
    coefficients: Dict[str, Any],
    price_overrides: Dict[Tuple[str, float], float],
    customer: str = "",
    project: str = "",
    source_file: str = "",
    price_list_id: Optional[int] = None,
) -> int:
    if supabase_store.enabled():
        try:
            return supabase_store.save_quotation_session(
                name,
                items,
                summary,
                coefficients,
                price_overrides,
                customer=customer,
                project=project,
                source_file=source_file,
                price_list_id=str(price_list_id) if price_list_id else None,
            )
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase save_quotation_session fallback to SQLite: {exc}")
    serializable_prices = {f"{material}|{thickness}": price for (material, thickness), price in price_overrides.items()}
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO quotation_sessions
        (name, customer, project, source_file, price_list_id, items_json, summary_json, coefficients_json, price_overrides_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name,
        customer,
        project,
        source_file,
        price_list_id,
        json.dumps(items, ensure_ascii=False),
        json.dumps(summary, ensure_ascii=False),
        json.dumps(coefficients, ensure_ascii=False),
        json.dumps(serializable_prices, ensure_ascii=False),
    ))
    session_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return int(session_id)


def list_quotation_sessions(limit: int = 30) -> List[Dict[str, Any]]:
    if supabase_store.enabled():
        try:
            return supabase_store.list_quotation_sessions(limit)
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase list_quotation_sessions fallback to SQLite: {exc}")
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT id, name, customer, project, source_file, created_at, updated_at
        FROM quotation_sessions
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def load_quotation_session(session_id: int | str) -> Optional[Dict[str, Any]]:
    if supabase_store.enabled():
        try:
            return supabase_store.load_quotation_session(str(session_id))
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase load_quotation_session fallback to SQLite: {exc}")
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM quotation_sessions WHERE id = ?", (session_id,)).fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    data["items"] = json.loads(data.pop("items_json"))
    data["summary"] = json.loads(data.pop("summary_json"))
    data["coefficients"] = json.loads(data.pop("coefficients_json") or "{}")
    encoded_prices = json.loads(data.pop("price_overrides_json") or "{}")
    data["price_overrides"] = {
        (material, float(thickness)): price
        for key, price in encoded_prices.items()
        for material, thickness in [key.split("|", 1)]
    }
    return data


def compare_items(current_items: List[Dict[str, Any]], old_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key_for(item: Dict[str, Any]) -> str:
        mark = str(item.get("mark") or "").strip().lower()
        if mark:
            return f"mark:{mark}"
        desc = re.sub(r"\s+", " ", str(item.get("description") or "").strip().lower())
        return f"desc:{desc[:120]}"

    current = {key_for(item): item for item in current_items}
    old = {key_for(item): item for item in old_items}
    rows: List[Dict[str, Any]] = []

    for key in sorted(set(current) | set(old)):
        cur = current.get(key)
        prev = old.get(key)
        if cur and not prev:
            rows.append({
                "status": "New",
                "trạng_thái": "Mới",
                "description": cur.get("description"),
                "mô_tả": cur.get("description"),
                "old_qty": 0,
                "new_qty": cur.get("quantity"),
                "change": cur.get("quantity"),
                "khối_lượng_cũ": 0,
                "khối_lượng_mới": cur.get("quantity"),
                "chênh_lệch": cur.get("quantity"),
                "size_change": "",
                "đổi_kích_thước": "",
            })
        elif prev and not cur:
            rows.append({
                "status": "Removed",
                "trạng_thái": "Đã bỏ",
                "description": prev.get("description"),
                "mô_tả": prev.get("description"),
                "old_qty": prev.get("quantity"),
                "new_qty": 0,
                "change": -float(prev.get("quantity") or 0),
                "khối_lượng_cũ": prev.get("quantity"),
                "khối_lượng_mới": 0,
                "chênh_lệch": -float(prev.get("quantity") or 0),
                "size_change": "",
                "đổi_kích_thước": "",
            })
        elif cur and prev:
            old_qty = float(prev.get("quantity") or 0)
            new_qty = float(cur.get("quantity") or 0)
            old_size = (prev.get("width"), prev.get("height"), prev.get("diameter"), prev.get("length"))
            new_size = (cur.get("width"), cur.get("height"), cur.get("diameter"), cur.get("length"))
            if abs(new_qty - old_qty) > 0.0001 or old_size != new_size:
                rows.append({
                    "status": "Changed",
                    "trạng_thái": "Thay đổi",
                    "description": cur.get("description") or prev.get("description"),
                    "mô_tả": cur.get("description") or prev.get("description"),
                    "old_qty": old_qty,
                    "new_qty": new_qty,
                    "change": new_qty - old_qty,
                    "khối_lượng_cũ": old_qty,
                    "khối_lượng_mới": new_qty,
                    "chênh_lệch": new_qty - old_qty,
                    "size_change": f"{old_size} -> {new_size}" if old_size != new_size else "",
                    "đổi_kích_thước": f"{old_size} -> {new_size}" if old_size != new_size else "",
                })
    return rows


def evaluate_quote_readiness(
    items: List[Dict[str, Any]],
    summary: Dict[str, Any],
    warnings: List[str],
    price_overrides: Dict[Tuple[str, float], float],
    settings: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    settings = settings or get_company_settings()
    blockers: List[str] = []
    questions: List[str] = []
    actions: List[str] = []

    unknown_items = [
        item for item in items
        if item.get("category") == "UNKNOWN"
        and not item.get("reference_unit_price")
        and item.get("reference_source") != "AI quote memory"
        and item.get("reference_source") != "Nhập tay"
    ]
    zero_price_set = set()
    for item in items:
        if item.get("reference_unit_price"):
            continue
        if item.get("reference_source") == "AI quote memory":
            continue
        if item.get("reference_source") == "Nhập tay":
            continue
        if item.get("pricing_mode") == "piece" and float(item.get("quote_unit_price") or 0) > 0:
            continue
        key = (item.get("material", "GI"), float(item.get("thickness") or 0))
        if price_overrides.get(key, 0) <= 0:
            zero_price_set.add(key)
    zero_prices = sorted(zero_price_set)
    invalid_qty = [item for item in items if float(item.get("quantity") or 0) <= 0]
    unsafe_priced_items = [
        item for item in items
        if not item.get("can_auto_quote")
        and item.get("reference_source") not in {"AI quote memory", "Nhập tay", "Báo giá cũ"}
    ]
    missing_size = [
        item for item in items
        if not item.get("reference_unit_price")
        if item.get("reference_source") != "AI quote memory"
        if item.get("reference_source") != "Nhập tay"
        if item.get("category") not in ["LOUVER", "SQUARE_DIFFUSER", "ROUND_DIFFUSER", "FIRE_DAMPER", "MOTORIZED_DAMPER"]
        and not item.get("width") and not item.get("diameter")
    ]

    profit = float(summary.get("profit") or 0)
    subtotal = float(summary.get("subtotal") or 0)
    profit_ratio = profit / subtotal if subtotal else 0
    min_profit = float(settings.get("min_profit_pct", "0") or 0)

    if settings.get("require_no_unknown_products", "1") == "1" and unknown_items:
        blockers.append(f"Còn {len(unknown_items)} sản phẩm chưa xác định danh mục.")
        questions.append("Vui lòng xác định danh mục cho các sản phẩm chưa nhận diện.")
        actions.append("Mở Quản trị quy tắc hoặc bổ sung từ khóa sản phẩm để lần sau tự phân loại.")

    if settings.get("require_all_prices", "1") == "1" and zero_prices:
        blockers.append(f"Còn {len(zero_prices)} cặp vật liệu/độ dày thiếu đơn giá hoặc đơn giá bằng 0.")
        for material, thickness in zero_prices[:6]:
            questions.append(f"Đơn giá cho {material} {thickness:g} mm là bao nhiêu?")

    if invalid_qty:
        blockers.append(f"Có {len(invalid_qty)} dòng có khối lượng không hợp lệ.")
        questions.append("Vui lòng xác nhận các dòng có khối lượng bằng 0 hoặc âm.")

    if unsafe_priced_items:
        blockers.append(f"Có {len(unsafe_priced_items)} dòng AI chưa đủ cơ sở công thức/giá để tự báo giá.")
        questions.append("Vui lòng xác nhận mã sản phẩm chuẩn, rule tính hoặc đơn giá cho các dòng AI chưa chắc chắn.")
        actions.append("Mở tab Trợ lý sales/QS để trả lời các dòng chưa báo giá được, sau đó chạy lại phân tích.")

    if missing_size:
        blockers.append(f"Có {len(missing_size)} dòng thiếu dữ liệu kích thước.")
        questions.append("Vui lòng xác nhận Rộng/Cao/Đường kính/Dài cho các dòng chưa đọc được kích thước.")

    if warnings:
        actions.append("Kiểm tra các cảnh báo trước khi phát hành báo giá.")

    if profit_ratio < min_profit:
        blockers.append(f"Tỷ lệ lợi nhuận {profit_ratio:.1%} thấp hơn mức tối thiểu của công ty {min_profit:.1%}.")
        questions.append("Bạn muốn tăng lợi nhuận, chỉnh chi phí hay xin quản lý phê duyệt?")

    score = 100
    score -= min(len(blockers) * 18, 72)
    score -= min(len(warnings) * 2, 20)
    score = max(score, 0)
    status = "READY" if score >= 85 and not blockers else "NEEDS_INPUT" if score >= 50 else "BLOCKED"

    return {
        "score": score,
        "status": status,
        "blockers": blockers,
        "questions": questions,
        "actions": actions,
        "unknown_count": len(unknown_items),
        "missing_price_count": len(zero_prices),
        "invalid_qty_count": len(invalid_qty),
        "missing_size_count": len(missing_size),
        "unsafe_price_count": len(unsafe_priced_items),
        "profit_ratio": profit_ratio,
    }


def estimator_brief(
    items: List[Dict[str, Any]],
    summary: Dict[str, Any],
    warnings: List[str],
    readiness: Dict[str, Any],
    settings: Optional[Dict[str, str]] = None,
) -> str:
    settings = settings or get_company_settings()
    lines = [
        f"Công ty: {settings.get('company_name', '')}",
        f"Trạng thái: {_vi_status(readiness['status'])} | Điểm sẵn sàng: {readiness['score']}/100",
        f"Số dòng: {len(items)} | Chưa xác định: {readiness['unknown_count']} | Thiếu giá: {readiness['missing_price_count']}",
        f"Tổng phụ: {summary.get('subtotal', 0):,.2f} | Tỷ lệ lợi nhuận: {readiness['profit_ratio']:.1%} | Tổng cộng: {summary.get('grand_total', 0):,.2f}",
        "",
        "Câu hỏi cho QS/sales/khách hàng:",
    ]
    lines.extend(f"- {q}" for q in readiness["questions"][:10])
    if not readiness["questions"]:
        lines.append("- Không phát hiện thiếu đầu vào kỹ thuật/thương mại.")
    lines.append("")
    lines.append("Việc cần xử lý:")
    lines.extend(f"- {a}" for a in readiness["actions"][:10])
    if not readiness["actions"]:
        lines.append("- Đủ điều kiện phê duyệt/xuất file theo chính sách hiện tại.")
    if warnings:
        lines.append("")
        lines.append("Cảnh báo:")
        lines.extend(f"- {warning}" for warning in warnings[:10])
    return "\n".join(lines)


def save_approval(
    status: str,
    project: str,
    customer: str,
    approver: str,
    notes: str,
    readiness_score: float,
    session_id: Optional[int] = None,
) -> int | str:
    if supabase_store.enabled():
        try:
            return supabase_store.save_approval(
                status,
                project,
                customer,
                approver,
                notes,
                readiness_score,
                session_id=str(session_id) if session_id else None,
            )
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase save_approval fallback to SQLite: {exc}")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO quotation_approvals
        (session_id, project, customer, status, approver, notes, readiness_score)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (session_id, project, customer, status, approver, notes, readiness_score))
    approval_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return int(approval_id)


def list_approvals(limit: int = 50) -> List[Dict[str, Any]]:
    if supabase_store.enabled():
        try:
            return supabase_store.list_approvals(limit)
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase list_approvals fallback to SQLite: {exc}")
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT id, session_id, project, customer, status, approver, notes, readiness_score, created_at
        FROM quotation_approvals
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def has_final_approval(project: str, customer: str) -> bool:
    if supabase_store.enabled():
        try:
            return supabase_store.has_final_approval(project, customer)
        except Exception as exc:
            if _supabase_strict_enabled():
                raise
            print(f"Supabase has_final_approval fallback to SQLite: {exc}")
    conn = get_db_connection()
    row = conn.execute("""
        SELECT id FROM quotation_approvals
        WHERE project = ? AND customer = ? AND status = 'APPROVED'
        ORDER BY created_at DESC
        LIMIT 1
    """, (project, customer)).fetchone()
    conn.close()
    return row is not None


def build_audit_report(
    items: List[Dict[str, Any]],
    summary: Dict[str, Any],
    readiness: Dict[str, Any],
    settings: Dict[str, str],
) -> str:
    return "\n".join([
        "# Biên bản kiểm tra báo giá HVAC",
        "<!-- HVAC Quotation Audit Report -->",
        "",
        f"Công ty: {settings.get('company_name', '')}",
        f"Trạng thái: {_vi_status(readiness['status'])}",
        f"Điểm sẵn sàng: {readiness['score']}/100",
        f"Tổng số dòng: {len(items)}",
        f"Sản phẩm chưa xác định: {readiness['unknown_count']}",
        f"Thiếu giá: {readiness['missing_price_count']}",
        f"Dòng sai khối lượng: {readiness['invalid_qty_count']}",
        f"Dòng thiếu kích thước: {readiness['missing_size_count']}",
        f"Tỷ lệ lợi nhuận: {readiness['profit_ratio']:.2%}",
        f"Tổng cộng: {summary.get('grand_total', 0):,.2f}",
        "",
        "## Điểm chặn",
        *(f"- {item}" for item in readiness["blockers"]),
        "",
        "## Câu hỏi",
        *(f"- {item}" for item in readiness["questions"]),
        "",
        "## Việc cần xử lý",
        *(f"- {item}" for item in readiness["actions"]),
    ])


def explain_item(item: Dict[str, Any], coefficients: Dict[str, Any], unit_price: float) -> str:
    qty = float(item.get("quantity") or 0)
    area = float(item.get("calculated_area") or 0)
    area_per_item = area / qty if qty else area
    material = float(item.get("material_cost") or 0)
    subtotal = float(item.get("subtotal") or 0)
    return "\n".join([
        f"Dòng: {item.get('mark')} - {item.get('description')}",
        f"Nhóm: {item.get('category')} | Vật liệu: {item.get('material')} | Độ dày: {item.get('thickness')} mm",
        f"Kích thước: Rộng={item.get('width')} Cao={item.get('height')} ĐK={item.get('diameter')} Dài={item.get('length')} mm",
        f"Kết quả diện tích: {area_per_item:.3f} m2/dòng x {qty:g} = {area:.3f} m2",
        f"Nguồn đơn giá vật liệu: {unit_price:,.2f} / m2",
        f"Chi phí vật liệu: {area:.3f} x {unit_price:,.2f} = {material:,.2f}",
        f"Nhân công: {item.get('labor_cost', 0):,.2f} | Phụ kiện: {item.get('accessory_cost', 0):,.2f} | Lắp đặt: {item.get('installation_cost', 0):,.2f}",
        f"Sơn: {item.get('painting_cost', 0):,.2f} | Bảo ôn: {item.get('insulation_cost', 0):,.2f} | Hao hụt: {item.get('waste_cost', 0):,.2f}",
        f"Tổng phụ: {subtotal:,.2f} | Lợi nhuận: {item.get('profit', 0):,.2f} | VAT: {item.get('vat', 0):,.2f}",
        f"Tổng cộng: {item.get('grand_total', 0):,.2f}",
        f"Hệ số đang dùng: {json.dumps(coefficients, ensure_ascii=False)}",
    ])


def build_simple_pdf(title: str, items: List[Dict[str, Any]], summary: Dict[str, Any], signer: str = "") -> bytes:
    labels = {
        "total_area": "Diện tích",
        "subtotal": "Tổng phụ",
        "profit": "Lợi nhuận",
        "vat": "VAT",
        "grand_total": "Tổng cộng",
    }
    lines = [title, f"Ngày tạo: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    for key in ["total_area", "subtotal", "profit", "vat", "grand_total"]:
        lines.append(f"{labels[key]}: {summary.get(key, 0):,.2f}")
    lines.append("")
    for idx, item in enumerate(items[:60], start=1):
        lines.append(f"{idx}. {item.get('description')} | KL {item.get('quantity')} {item.get('unit')} | Tổng {item.get('grand_total', 0):,.2f}")
    if len(items) > 60:
        lines.append(f"... còn {len(items) - 60} dòng trong file Excel")
    lines.append("")
    lines.append(f"Chữ ký / phê duyệt: {signer or 'Chờ phê duyệt'}")

    text = "\\n".join(line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines)
    content = f"BT /F1 10 Tf 50 790 Td 12 TL ({text}) Tj ET".encode("latin-1", errors="replace")
    stream = b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        stream,
    ]
    output = BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{idx} 0 obj\n".encode())
        output.write(obj)
        output.write(b"\nendobj\n")
    xref = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode())
    output.write(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return output.getvalue()


def _vi_status(status: str) -> str:
    return {
        "READY": "SẴN SÀNG",
        "NEEDS_INPUT": "CẦN BỔ SUNG",
        "BLOCKED": "BỊ CHẶN",
        "DRAFT": "BẢN NHÁP",
        "NEEDS_REVISION": "CẦN SỬA",
        "APPROVED": "ĐÃ DUYỆT",
        "REJECTED": "TỪ CHỐI",
    }.get(str(status or ""), str(status or ""))

