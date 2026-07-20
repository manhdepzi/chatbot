from __future__ import annotations

import os
import hashlib
import tempfile
import importlib
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import backend.intelligence as intelligence_module
import backend.parser_engine as parser_engine_module
import backend.pricing_engine as pricing_engine_module
import backend.quote_memory as quote_memory_module
import backend.supabase_store as supabase_store

importlib.reload(supabase_store)
importlib.reload(intelligence_module)
importlib.reload(parser_engine_module)
importlib.reload(quote_memory_module)
importlib.reload(pricing_engine_module)

from backend.database import get_db_connection, init_db
from backend.excel_engine import build_quotation_workbook
from backend.intelligence import (
    build_simple_pdf,
    build_audit_report,
    compare_items,
    create_price_list,
    estimator_brief,
    evaluate_quote_readiness,
    explain_item,
    get_price_rules_for_list,
    get_company_settings,
    has_final_approval,
    learned_suggestions,
    learn_from_quote,
    list_approvals,
    list_company_settings,
    list_price_lists,
    list_quotation_sessions,
    load_quotation_session,
    save_approval,
    save_quotation_session,
    update_company_setting,
    upsert_price_list_item,
)
from backend.parser_engine import normalize_text, parse_customer_sheet, parse_reference_quote
from backend.llm_quote_enrichment import enrich_reference_items_with_llm
from backend.pricing_engine import get_coefficient_rules, run_project_pricing
from backend.quote_memory import list_quote_memory, train_quote_memory
from backend.product_catalog import (
    list_product_aliases,
    list_product_master,
    list_sales_answers,
    save_sales_product_answer,
    upsert_product_alias,
)


load_dotenv()
init_db()

st.set_page_config(page_title="Hệ thống báo giá HVAC AI", layout="wide")

st.markdown(
    """
    <style>
        .block-container {padding-top: 1.25rem; padding-bottom: 2rem;}
        .metric-card {border: 1px solid #dde5ef; border-radius: 8px; padding: 14px 16px; background: #fff;}
        .metric-label {font-size: 0.78rem; color: #667085; margin-bottom: 6px;}
        .metric-value {font-size: 1.2rem; font-weight: 700; color: #182230;}
    .status-ok {color: #067647; font-weight: 700;}
    .status-warn {color: #b54708; font-weight: 700;}
    .status-block {color: #b42318; font-weight: 700;}
    .small-note {color: #667085; font-size: 0.86rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


def _save_upload(uploaded_file) -> str:
    if uploaded_file is None:
        raise ValueError("Chưa có file để đọc. Hãy tải file KL/BOQ khách gửi rồi bấm Phân tích khối lượng.")
    suffix = Path(uploaded_file.name).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getbuffer())
        return tmp.name


def _upload_to_storage(uploaded_file, kind: str) -> None:
    if not uploaded_file or not supabase_store.enabled():
        return
    try:
        result = supabase_store.upload_project_file(
            uploaded_file.name,
            uploaded_file.getvalue(),
            kind,
            project=st.session_state.get("project", ""),
            customer=st.session_state.get("customer", ""),
            mime_type=getattr(uploaded_file, "type", None),
        )
        if result:
            st.session_state.setdefault("uploaded_project_files", []).append(result)
    except Exception as exc:
        st.warning(f"Chưa lưu được file {uploaded_file.name} lên Supabase Storage: {type(exc).__name__}: {exc}")


def _parse_uploaded(uploaded_file) -> List[Dict[str, Any]]:
    path = _save_upload(uploaded_file)
    try:
        return parse_customer_sheet(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _parse_reference_uploaded(uploaded_file) -> List[Dict[str, Any]]:
    path = _save_upload(uploaded_file)
    try:
        return parse_reference_quote(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _render_bulk_completed_quote_import(location_key: str = "main") -> None:
    st.subheader("Import hàng loạt báo giá đã hoàn thành")
    st.caption(
        "Dùng màn hình này để nạp nhiều file báo giá đã làm vào Supabase. "
        "App sẽ đọc Excel bằng parser nội bộ, không dùng LLM cho các dòng đọc được, nên gần như không tốn token."
    )
    files = st.file_uploader(
        "Chọn nhiều file báo giá đã hoàn thành",
        type=["xlsx", "xls"],
        accept_multiple_files=True,
        key=f"bulk_completed_quotes_{location_key}",
        help="Có thể chọn hàng chục hoặc hàng trăm file Excel cùng lúc.",
    )
    col_a, col_b = st.columns(2)
    source_project = col_a.text_input("Tên dự án nguồn mặc định", value=st.session_state.get("project", ""), key=f"bulk_project_{location_key}")
    source_customer = col_b.text_input("Khách hàng nguồn mặc định", value=st.session_state.get("customer", ""), key=f"bulk_customer_{location_key}")
    save_storage = st.checkbox(
        "Lưu bản file gốc lên Supabase Storage",
        value=True,
        key=f"bulk_storage_{location_key}",
        help="Nên bật khi chạy thật để sau này truy vết nguồn giá.",
    )
    use_llm = st.checkbox(
        "Dùng AI/LLM xử lý dòng thiếu mã sản phẩm, kích thước hoặc công thức",
        value=True,
        key=f"bulk_use_llm_{location_key}",
        help="Chỉ gửi các dòng parser chưa đủ chắc lên LLM. Dòng LLM suy luận vẫn cần sales/QS duyệt nếu độ tin cậy thấp.",
    )
    max_llm_rows = st.number_input(
        "Giới hạn dòng gửi LLM mỗi file",
        min_value=0,
        max_value=500,
        value=200,
        step=10,
        key=f"bulk_max_llm_{location_key}",
        disabled=not use_llm,
    )

    if not files:
        st.info("Chọn các file báo giá đã hoàn thành rồi bấm Import hàng loạt.")
        return

    st.write(f"Đã chọn {len(files)} file.")
    if st.button("Import hàng loạt vào bộ nhớ AI", type="primary", key=f"run_bulk_import_{location_key}"):
        results: List[Dict[str, Any]] = []
        all_skip_details: List[Dict[str, Any]] = []
        progress = st.progress(0, text="Bắt đầu import...")
        for idx, uploaded in enumerate(files, start=1):
            file_name = getattr(uploaded, "name", f"completed_quote_{idx}.xlsx")
            try:
                if save_storage:
                    _upload_to_storage(uploaded, "completed_quote")
                reference_items = _parse_reference_uploaded(uploaded)
                llm_info = {"processed": 0, "updated": 0, "warnings": []}
                if use_llm:
                    reference_items, llm_info = enrich_reference_items_with_llm(
                        reference_items,
                        max_items=int(max_llm_rows),
                    )
                diagnostics = _quote_memory_import_diagnostics(reference_items, file_name)
                all_skip_details.extend(diagnostics["details"])
                trained = train_quote_memory(
                    reference_items,
                    source_project=source_project,
                    source_customer=source_customer,
                    source_file=file_name,
                )
                status = "Đã học" if trained else "Không có dòng giá hợp lệ"
                results.append({
                    "file": file_name,
                    "trạng_thái": status,
                    "dòng_đọc_được": len(reference_items),
                    "dòng_đã_học": trained,
                    "dòng_có_thể_học": diagnostics["counts"]["có_thể_học"],
                    "bỏ_qua_UNKNOWN": diagnostics["counts"]["unknown"],
                    "bỏ_qua_thiếu_giá": diagnostics["counts"]["thiếu_giá"],
                    "bỏ_qua_LLM_thấp": diagnostics["counts"]["llm_thấp"],
                    "dòng_gửi_LLM": int(llm_info.get("processed") or 0),
                    "dòng_LLM_bổ_sung": int(llm_info.get("updated") or 0),
                    "lỗi": "",
                    "cảnh_báo_LLM": "; ".join(llm_info.get("warnings") or []),
                })
            except Exception as exc:
                results.append({
                    "file": file_name,
                    "trạng_thái": "Lỗi",
                    "dòng_đọc_được": 0,
                    "dòng_đã_học": 0,
                    "dòng_có_thể_học": 0,
                    "bỏ_qua_UNKNOWN": 0,
                    "bỏ_qua_thiếu_giá": 0,
                    "bỏ_qua_LLM_thấp": 0,
                    "dòng_gửi_LLM": 0,
                    "dòng_LLM_bổ_sung": 0,
                    "lỗi": f"{type(exc).__name__}: {exc}",
                    "cảnh_báo_LLM": "",
                })
            progress.progress(idx / max(len(files), 1), text=f"Đã xử lý {idx}/{len(files)} file")

        st.session_state[f"bulk_import_results_{location_key}"] = results
        st.session_state[f"bulk_import_skip_details_{location_key}"] = all_skip_details[:5000]
        total_read = sum(int(row.get("dòng_đọc_được") or 0) for row in results)
        total_trained = sum(int(row.get("dòng_đã_học") or 0) for row in results)
        total_errors = sum(1 for row in results if row.get("trạng_thái") == "Lỗi")
        total_skipped = max(total_read - total_trained, 0)
        total_unknown = sum(int(row.get("bỏ_qua_UNKNOWN") or 0) for row in results)
        total_missing_price = sum(int(row.get("bỏ_qua_thiếu_giá") or 0) for row in results)
        total_low_llm = sum(int(row.get("bỏ_qua_LLM_thấp") or 0) for row in results)
        if total_errors:
            st.warning(f"Hoàn tất import: đọc {total_read} dòng, học {total_trained} dòng, có {total_errors} file lỗi.")
        else:
            st.success(f"Hoàn tất import: đọc {total_read} dòng, học {total_trained} dòng.")
        if total_skipped:
            st.info(
                "Các dòng chưa học không bị mất: app đang chặn để tránh AI học sai. "
                f"Đã bỏ qua {total_skipped} dòng; trong đó chưa nhận diện sản phẩm: {total_unknown}, "
                f"thiếu giá bán: {total_missing_price}, LLM chưa đủ tự tin: {total_low_llm}. "
                "Mở bảng chi tiết bên dưới để bổ sung alias/rule hoặc gửi sales-QS duyệt."
            )

    results = st.session_state.get(f"bulk_import_results_{location_key}", [])
    if results:
        st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
        details = st.session_state.get(f"bulk_import_skip_details_{location_key}", [])
        if details:
            with st.expander("Chi tiết các dòng chưa được học / cần sales-QS duyệt", expanded=False):
                st.dataframe(pd.DataFrame(details), use_container_width=True, hide_index=True)


def _quote_memory_import_diagnostics(items: List[Dict[str, Any]], file_name: str) -> Dict[str, Any]:
    details: List[Dict[str, Any]] = []
    counts = {
        "thiếu_giá": 0,
        "unknown": 0,
        "llm_thấp": 0,
        "thiếu_product_code": 0,
        "thiếu_số_lượng": 0,
        "có_thể_học": 0,
    }
    for idx, item in enumerate(items, start=1):
        reasons = []
        unit_price = float(item.get("reference_unit_price") or item.get("quote_unit_price") or 0)
        qty = float(item.get("quantity") or 0)
        if unit_price <= 0:
            reasons.append("thiếu giá bán")
            counts["thiếu_giá"] += 1
        if qty <= 0:
            reasons.append("thiếu/sai số lượng")
            counts["thiếu_số_lượng"] += 1
        if str(item.get("category") or "UNKNOWN") == "UNKNOWN":
            reasons.append("chưa xác định category")
            counts["unknown"] += 1
        if item.get("llm_enriched") and float(item.get("llm_confidence") or 0) < 85:
            reasons.append(f"LLM confidence thấp ({float(item.get('llm_confidence') or 0):g})")
            counts["llm_thấp"] += 1
        if item.get("llm_enriched") and not item.get("product_code"):
            reasons.append("LLM chưa xác định product_code")
            counts["thiếu_product_code"] += 1

        if reasons:
            details.append({
                "file": file_name,
                "dòng": item.get("item_no") or idx,
                "mô_tả": item.get("description"),
                "category": item.get("category"),
                "product_code": item.get("product_code"),
                "đơn_giá": unit_price,
                "số_lượng": qty,
                "lý_do_chưa_học": "; ".join(reasons),
                "llm_confidence": item.get("llm_confidence"),
                "llm_reason": item.get("llm_reason"),
            })
        else:
            counts["có_thể_học"] += 1
    return {"counts": counts, "details": details}


def _looks_like_completed_quote_file(file_name: str) -> bool:
    normalized = normalize_text(file_name)
    likely_quote = (
        "quotation" in normalized
        or "quote" in normalized
        or (
            "bao gia" in normalized
            and any(token in normalized for token in ["-ct", "_ct", " ct", "hoan thanh", "da lam", "chao gia"])
        )
    )
    likely_takeoff = any(token in normalized for token in ["kl", "boq", "khoi luong", "ong gio", "vat tu"])
    return likely_quote and not likely_takeoff


def _items_to_frame(items: List[Dict[str, Any]]) -> pd.DataFrame:
    columns = [
        "item_no",
        "code",
        "mark",
        "product_code",
        "category",
        "description",
        "width",
        "height",
        "diameter",
        "length",
        "thickness",
        "material",
        "pressure_class",
        "quantity",
        "unit",
        "quote_unit_price",
        "reference_source",
        "price_source_status",
        "pricing_confidence",
        "calculated_area",
        "material_cost",
        "labor_cost",
        "accessory_cost",
        "installation_cost",
        "subtotal",
        "grand_total",
        "warnings",
    ]
    rows = []
    for item in items:
        row = {col: item.get(col) for col in columns}
        if isinstance(row.get("warnings"), list):
            row["warnings"] = "; ".join(row["warnings"])
        rows.append(row)
    labels = {
        "item_no": "STT",
        "code": "Mã",
        "mark": "Ký hiệu",
        "product_code": "Mã sản phẩm chuẩn",
        "category": "Nhóm sản phẩm",
        "description": "Tên vật tư",
        "width": "Rộng",
        "height": "Cao",
        "diameter": "Đường kính",
        "length": "Dài",
        "thickness": "Độ dày",
        "material": "Vật liệu",
        "pressure_class": "Cấp áp",
        "quantity": "Khối lượng",
        "unit": "Đơn vị",
        "quote_unit_price": "Đơn giá bán",
        "reference_source": "Nguồn giá",
        "price_source_status": "Trạng thái nguồn giá",
        "pricing_confidence": "Độ chắc chắn",
        "calculated_area": "Diện tích m2",
        "material_cost": "Tiền vật tư",
        "labor_cost": "Nhân công",
        "accessory_cost": "Phụ kiện",
        "installation_cost": "Lắp đặt",
        "subtotal": "Thành tiền",
        "grand_total": "Tổng sau thuế",
        "warnings": "Cảnh báo",
    }
    return pd.DataFrame(rows, columns=columns).rename(columns=labels)


def _group_items(items: List[Dict[str, Any]]) -> pd.DataFrame:
    grouped: Dict[tuple, Dict[str, Any]] = defaultdict(
        lambda: {"quantity": 0.0, "area": 0.0, "subtotal": 0.0, "grand_total": 0.0}
    )
    for item in items:
        key = (
            item.get("category") or "UNKNOWN",
            item.get("thickness") or 0,
            item.get("material") or "",
            item.get("pressure_class") or "",
        )
        grouped[key]["quantity"] += float(item.get("quantity") or 0)
        grouped[key]["area"] += float(item.get("calculated_area") or 0)
        grouped[key]["subtotal"] += float(item.get("subtotal") or 0)
        grouped[key]["grand_total"] += float(item.get("grand_total") or 0)

    return pd.DataFrame(
        [
            {
                    "Nhóm sản phẩm": category,
                    "Độ dày": thickness,
                    "Vật liệu": material,
                    "Cấp áp": pressure,
                    "Khối lượng": round(totals["quantity"], 3),
                    "Diện tích": round(totals["area"], 3),
                    "Thành tiền": round(totals["subtotal"], 2),
                    "Tổng cộng": round(totals["grand_total"], 2),
            }
            for (category, thickness, material, pressure), totals in grouped.items()
        ]
    )


def _coefficient_editor() -> Dict[str, float]:
    coeffs = get_coefficient_rules()
    overrides: Dict[str, float] = {}
    col1, col2, col3 = st.columns(3)
    field_map = [
        ("labor_rate_pct", "Nhân công %", col1),
        ("installation_rate_pct", "Lắp đặt %", col1),
        ("accessory_rate_pct", "Phụ kiện %", col1),
        ("waste_rate_pct", "Hao hụt %", col2),
        ("management_fee_pct", "Quản lý %", col2),
        ("risk_fee_pct", "Rủi ro %", col2),
        ("profit_pct", "Lợi nhuận %", col3),
        ("vat_pct", "VAT %", col3),
        ("transportation_total", "Vận chuyển", col3),
        ("machinery_total", "Máy móc", col3),
    ]
    for key, label, column in field_map:
        default = float(coeffs.get(key, {}).get("value", 0.0))
        with column:
            overrides[key] = st.number_input(label, min_value=0.0, value=default, step=0.01, format="%.4f")
    return overrides


def _vi_price_list_name(name: str) -> str:
    translations = {
        "Default HVAC Price List": "Bảng giá HVAC mặc định",
        "Internal": "Nội bộ",
    }
    result = str(name or "")
    for source, target in translations.items():
        result = result.replace(source, target)
    return result


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


def _select_price_list() -> int | None:
    price_lists = list_price_lists()
    if not price_lists:
        return None
    labels = {
        f"{_vi_price_list_name(row['name'])} | {row.get('supplier') or '-'} | {row.get('region') or '-'}": row["id"]
        for row in price_lists
    }
    default_label = next((label for label, ident in labels.items() if any(row["id"] == ident and row["is_default"] for row in price_lists)), list(labels)[0])
    chosen = st.selectbox("Bảng giá", list(labels), index=list(labels).index(default_label))
    return labels[chosen]


def _price_editor(items: List[Dict[str, Any]], price_list_id: int | None) -> Dict[Tuple[str, float], float]:
    base_prices = get_price_rules_for_list(price_list_id)
    suggestions = learned_suggestions(items)
    detected = sorted(
        {
            (item.get("material", "GI"), float(item.get("thickness") or 0))
            for item in items
            if item.get("thickness")
        }
    )
    overrides: Dict[Tuple[str, float], float] = {}
    for material, thickness in detected:
        default = float(base_prices.get((material, thickness), 0.0))
        suggestion = next(
            (
                s.get("unit_price")
                for item in items
                for sig, s in suggestions.items()
                if item.get("material") == material and float(item.get("thickness") or 0) == thickness and s.get("unit_price")
            ),
            None,
        )
        help_text = f"Bộ nhớ AI gợi ý {suggestion:,.2f}" if suggestion else None
        overrides[(material, thickness)] = st.number_input(
            f"Đơn giá {material} {thickness:g} mm / m2",
            min_value=0.0,
            value=float(suggestion or default),
            step=1.0,
            format="%.2f",
            help=help_text,
        )
    return overrides


def _missing_price_messages(items: List[Dict[str, Any]], price_overrides: Dict[Tuple[str, float], float]) -> List[str]:
    missing = set()
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
            missing.add(key)
    return [f"Thiếu đơn giá vật liệu {material} dày {thickness:g} mm." for material, thickness in sorted(missing)]


def _show_metrics(summary: Dict[str, Any]) -> None:
    metric_cols = st.columns(5)
    metric_data = [
        ("Khu vực", f"{summary.get('total_area', 0):,.2f} m2"),
        ("Vật liệu", f"{summary.get('total_material', 0):,.2f}"),
        ("Tổng phụ", f"{summary.get('subtotal', 0):,.2f}"),
        ("VAT", f"{summary.get('vat', 0):,.2f}"),
        ("Tổng cộng", f"{summary.get('grand_total', 0):,.2f}"),
    ]
    for column, (label, value) in zip(metric_cols, metric_data):
        column.markdown(
            f"<div class='metric-card'><div class='metric-label'>{label}</div><div class='metric-value'>{value}</div></div>",
            unsafe_allow_html=True,
        )


def _reference_key(item: Dict[str, Any]) -> str:
    desc = normalize_text(item.get("description", ""))
    desc = desc.replace("lap dat ", "").strip()
    return desc[:140]


def _apply_reference_prices(
    calculated_items: List[Dict[str, Any]],
    reference_items: List[Dict[str, Any]],
    vat_rate: float,
) -> tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    by_desc = {_reference_key(item): item for item in reference_items if item.get("reference_unit_price")}
    by_item_no = {
        str(item.get("item_no") or "").replace(".0", ""): item
        for item in reference_items
        if item.get("item_no") and item.get("reference_unit_price")
    }

    matched = 0
    updated_items: List[Dict[str, Any]] = []
    for item in calculated_items:
        key = _reference_key(item)
        item_no = str(item.get("item_no") or "").replace(".0", "")
        ref = by_item_no.get(item_no) or by_desc.get(key)
        updated = dict(item)
        if ref:
            unit_price = float(ref.get("reference_unit_price") or 0)
            qty = float(updated.get("quantity") or 0)
            line_total = qty * unit_price
            updated.update({
                "quote_unit_price": round(unit_price, 2),
                "reference_unit_price": round(unit_price, 2),
                "reference_source": "Báo giá cũ",
                "price_source_status": "reference_quote",
                "pricing_confidence": "high",
                "can_auto_quote": True,
                "warnings": [],
                "material_cost": round(line_total, 2),
                "labor_cost": 0.0,
                "accessory_cost": 0.0,
                "installation_cost": 0.0,
                "painting_cost": 0.0,
                "insulation_cost": 0.0,
                "waste_cost": 0.0,
                "subtotal": round(line_total, 2),
                "profit": 0.0,
                "vat": 0.0,
                "grand_total": round(line_total, 2),
            })
            matched += 1
        updated_items.append(updated)

    items_subtotal = sum(float(item.get("subtotal") or 0) for item in updated_items)
    vat = items_subtotal * vat_rate
    summary = {
        "total_area": round(sum(float(item.get("calculated_area") or 0) for item in updated_items), 2),
        "total_material": round(items_subtotal, 2),
        "total_labor": 0.0,
        "total_accessory": 0.0,
        "total_installation": 0.0,
        "total_painting": 0.0,
        "total_insulation": 0.0,
        "total_waste": 0.0,
        "items_subtotal": round(items_subtotal, 2),
        "transportation": 0.0,
        "machinery": 0.0,
        "management_fee": 0.0,
        "risk_fee": 0.0,
        "subtotal": round(items_subtotal, 2),
        "profit": 0.0,
        "vat": round(vat, 2),
        "grand_total": round(items_subtotal + vat, 2),
    }
    warnings = [f"Applied old quote unit prices to {matched}/{len(calculated_items)} current lines."]
    if matched < len(calculated_items):
        warnings.append(f"{len(calculated_items) - matched} lines did not match old quote prices and still use calculated/default pricing.")
    return updated_items, summary, warnings


def _apply_manual_unit_prices(
    calculated_items: List[Dict[str, Any]],
    manual_prices: Dict[str, float],
    vat_rate: float,
) -> tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    updated_items: List[Dict[str, Any]] = []
    applied = 0
    for idx, item in enumerate(calculated_items):
        updated = dict(item)
        unit_price = float(manual_prices.get(str(idx), 0) or 0)
        if unit_price > 0:
            qty = float(updated.get("quantity") or 0)
            line_total = qty * unit_price
            updated.update({
                "quote_unit_price": round(unit_price, 2),
                "reference_source": "Nhập tay",
                "price_source_status": "manual",
                "pricing_confidence": "approved_by_sales",
                "can_auto_quote": True,
                "material_cost": round(line_total, 2),
                "labor_cost": 0.0,
                "accessory_cost": 0.0,
                "installation_cost": 0.0,
                "painting_cost": 0.0,
                "insulation_cost": 0.0,
                "waste_cost": 0.0,
                "subtotal": round(line_total, 2),
                "profit": 0.0,
                "vat": 0.0,
                "grand_total": round(line_total, 2),
            })
            applied += 1
        updated_items.append(updated)

    items_subtotal = sum(float(item.get("subtotal") or 0) for item in updated_items)
    vat = items_subtotal * vat_rate
    summary = {
        "total_area": round(sum(float(item.get("calculated_area") or 0) for item in updated_items), 2),
        "total_material": round(items_subtotal, 2),
        "total_labor": 0.0,
        "total_accessory": 0.0,
        "total_installation": 0.0,
        "total_painting": 0.0,
        "total_insulation": 0.0,
        "total_waste": 0.0,
        "items_subtotal": round(items_subtotal, 2),
        "transportation": 0.0,
        "machinery": 0.0,
        "management_fee": 0.0,
        "risk_fee": 0.0,
        "subtotal": round(items_subtotal, 2),
        "profit": 0.0,
        "vat": round(vat, 2),
        "grand_total": round(items_subtotal + vat, 2),
        "manual_price_count": applied,
    }
    return updated_items, summary, [f"Đã áp đơn giá nhập tay cho {applied}/{len(calculated_items)} dòng."] if applied else []


def _manual_price_editor(items: List[Dict[str, Any]]) -> Dict[str, float]:
    rows = []
    saved = st.session_state.get("manual_unit_prices", {})
    for idx, item in enumerate(items):
        rows.append({
            "row_id": str(idx),
            "STT": item.get("item_no") or idx + 1,
            "Tên vật tư": item.get("description"),
            "Đơn vị": item.get("unit"),
            "Khối lượng": float(item.get("quantity") or 0),
            "Đơn giá hiện tại": float(item.get("quote_unit_price") or 0),
            "Đơn giá nhập tay": float(saved.get(str(idx), 0) or 0),
            "Nguồn giá": item.get("reference_source") or "",
        })
    frame = pd.DataFrame(rows)
    edited = st.data_editor(
        frame,
        use_container_width=True,
        hide_index=True,
        disabled=["row_id", "STT", "Tên vật tư", "Đơn vị", "Khối lượng", "Đơn giá hiện tại", "Nguồn giá"],
        key="manual_unit_price_editor",
    )
    manual_prices = {
        str(row["row_id"]): float(row.get("Đơn giá nhập tay") or 0)
        for _, row in edited.iterrows()
        if float(row.get("Đơn giá nhập tay") or 0) > 0
    }
    st.session_state.manual_unit_prices = manual_prices
    return manual_prices


def _unsafe_quote_items(items: List[Dict[str, Any]]) -> List[Tuple[int, Dict[str, Any], str]]:
    unsafe = []
    for idx, item in enumerate(items):
        reasons = []
        if not item.get("can_auto_quote"):
            reasons.append("AI chưa đủ cơ sở để tự báo giá")
        if item.get("category") == "UNKNOWN":
            reasons.append("chưa xác định danh mục")
        if float(item.get("quote_unit_price") or 0) <= 0:
            reasons.append("thiếu đơn giá bán")
        if item.get("price_source_status") in {
            "closest_thickness",
            "missing_material_price",
            "missing_required_fields",
            "needs_composite_rule",
            "needs_manual_price",
            "unknown_product",
        }:
            reasons.append(str(item.get("price_source_status")))
        if item.get("reference_source") in {"RAG bộ nhớ AI", "AI quote memory composite"}:
            reasons.append("nguồn giá cần QS xác nhận")
        if reasons:
            unsafe.append((idx, item, "; ".join(dict.fromkeys(reasons))))
    return unsafe


def _render_sales_qs_chatbot(items: List[Dict[str, Any]]) -> None:
    st.subheader("Trợ lý sales/QS")
    st.caption(
        "AI chỉ báo giá tự động với dòng có công thức/đơn giá đủ chắc. "
        "Các dòng dưới đây cần sales/QS xác nhận mã sản phẩm hoặc nhập đơn giá trước khi xuất chính thức."
    )

    unsafe = _unsafe_quote_items(items)
    if not unsafe:
        st.success("Không còn dòng nào cần sales/QS trả lời. Có thể chuyển qua checklist/phê duyệt.")
    else:
        st.warning(f"Còn {len(unsafe)} dòng AI chưa được phép tự báo giá.")

    product_rows = list_product_master()
    product_options = [""] + [f"{row['product_code']} | {row['display_name_vi']}" for row in product_rows]
    product_category = {row["product_code"]: row["category"] for row in product_rows}

    for idx, item, reason in unsafe[:30]:
        label = f"Dòng {item.get('item_no') or idx + 1}: {item.get('description')}"
        with st.expander(label, expanded=idx == unsafe[0][0]):
            st.write({
                "lý_do": reason,
                "mã_AI_đang_nhận": item.get("product_code") or "",
                "nhóm_AI_đang_nhận": item.get("category"),
                "alias_khớp": item.get("matched_alias") or "",
                "kích_thước": {
                    "W": item.get("width"),
                    "H": item.get("height"),
                    "D": item.get("diameter"),
                    "L": item.get("length"),
                },
                "khối_lượng": item.get("quantity"),
                "đơn_vị": item.get("unit"),
                "đơn_giá_hiện_tại": item.get("quote_unit_price"),
                "nguồn_giá": item.get("reference_source") or item.get("price_source_status"),
            })
            selected = st.selectbox(
                "Mã sản phẩm chuẩn",
                product_options,
                key=f"sales_product_code_{idx}",
                help="Chọn mã chuẩn để lần sau AI map đúng sản phẩm này.",
            )
            unit_price = st.number_input(
                "Đơn giá bán đã được sales/QS xác nhận",
                min_value=0.0,
                value=float(st.session_state.get("manual_unit_prices", {}).get(str(idx), 0) or 0),
                step=1000.0,
                format="%.2f",
                key=f"sales_unit_price_{idx}",
            )
            note = st.text_area("Ghi chú cơ sở giá/công thức", key=f"sales_note_{idx}")
            approved_by = st.text_input("Người xác nhận", key=f"sales_approved_by_{idx}")
            save_alias = st.checkbox(
                "Lưu mô tả dòng này thành alias cho lần sau",
                value=False,
                key=f"sales_save_alias_{idx}",
            )
            if st.button("Lưu câu trả lời cho dòng này", key=f"save_sales_answer_{idx}"):
                product_code = selected.split("|", 1)[0].strip() if selected else ""
                category = product_category.get(product_code, item.get("category"))
                if unit_price <= 0:
                    st.error("Cần nhập đơn giá bán lớn hơn 0.")
                else:
                    manual_prices = dict(st.session_state.get("manual_unit_prices", {}))
                    manual_prices[str(idx)] = float(unit_price)
                    st.session_state.manual_unit_prices = manual_prices
                    if idx < len(st.session_state.get("takeoff_items", [])):
                        st.session_state.takeoff_items[idx].update({
                            "product_code": product_code,
                            "category": category,
                            "price_source_status": "manual",
                            "pricing_confidence": "approved_by_sales",
                            "can_auto_quote": True,
                        })
                    if idx < len(st.session_state.get("calculated_items", [])):
                        st.session_state.calculated_items[idx].update({
                            "product_code": product_code,
                            "category": category,
                            "quote_unit_price": float(unit_price),
                            "price_source_status": "manual",
                            "pricing_confidence": "approved_by_sales",
                            "can_auto_quote": True,
                            "reference_source": "Nhập tay",
                        })
                    save_sales_product_answer(
                        item,
                        product_code=product_code,
                        category=category,
                        unit_price=float(unit_price),
                        unit=str(item.get("unit") or ""),
                        note=note,
                        approved_by=approved_by,
                        save_alias=save_alias,
                    )
                    st.success("Đã lưu câu trả lời. Bấm Phân tích khối lượng hoặc Rerun để áp đơn giá vào báo giá.")

    with st.expander("Alias sản phẩm đã duyệt", expanded=False):
        st.dataframe(pd.DataFrame(list_product_aliases()), use_container_width=True, hide_index=True)
    with st.expander("Lịch sử câu trả lời sales/QS", expanded=False):
        st.dataframe(pd.DataFrame(list_sales_answers()), use_container_width=True, hide_index=True)


def _filter_reference_priced_warnings(warnings: List[str], items: List[Dict[str, Any]]) -> List[str]:
    reference_item_numbers = {
        str(item.get("item_no") or "").replace(".0", "")
        for item in items
        if item.get("reference_unit_price")
    }
    filtered = []
    for warning in warnings:
        matched_reference_line = any(warning.startswith(f"Item #{item_no} ") for item_no in reference_item_numbers)
        if not matched_reference_line:
            filtered.append(warning)
    return filtered


def _checklist_issue_id(kind: str, line_label: str, detail: str) -> str:
    raw = f"{kind}|{line_label}|{detail}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _build_sales_checklist(
    items: List[Dict[str, Any]],
    warnings: List[str],
    price_overrides: Dict[Tuple[str, float], float],
) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []

    def add_issue(kind: str, severity: str, line_label: str, description: str, detail: str) -> None:
        issues.append({
            "issue_id": _checklist_issue_id(kind, line_label, detail),
            "Mức độ": severity,
            "Dòng": line_label,
            "Vấn đề": description,
            "Chi tiết": detail,
            "Đã xử lý": False,
            "Ghi chú sales": "",
        })

    trusted_sources = {"AI quote memory", "RAG bộ nhớ AI", "Nhập tay", "Báo giá cũ", "Đối soát báo giá chuẩn"}
    no_size_required = {"LOUVER", "SQUARE_DIFFUSER", "ROUND_DIFFUSER", "FIRE_DAMPER", "MOTORIZED_DAMPER", "ADJUSTMENT"}

    for idx, item in enumerate(items):
        line_label = str(item.get("item_no") or idx + 1).replace(".0", "")
        desc = str(item.get("description") or "").strip()
        source = item.get("reference_source")
        price_source_status = item.get("price_source_status")

        if item.get("category") == "UNKNOWN" and source not in trusted_sources:
            add_issue("unknown", "Bắt buộc", line_label, "AI chưa xác định danh mục", desc)

        qty = float(item.get("quantity") or 0)
        if qty <= 0:
            add_issue("quantity", "Bắt buộc", line_label, "Số lượng không hợp lệ", f"{desc} | Số lượng: {qty:g}")

        if source not in trusted_sources:
            if item.get("pricing_mode") != "piece" or float(item.get("quote_unit_price") or 0) <= 0:
                key = (item.get("material", "GI"), float(item.get("thickness") or 0))
                if price_overrides.get(key, 0) <= 0:
                    add_issue(
                        "missing_price",
                        "Bắt buộc",
                        line_label,
                        "Thiếu đơn giá vật liệu/độ dày",
                        f"{desc} | {key[0]} {key[1]:g} mm",
                    )

        if price_source_status == "closest_thickness":
            add_issue(
                "closest_thickness",
                "Bắt buộc",
                line_label,
                "Đang dùng đơn giá độ dày gần nhất",
                f"{desc} | Cần nhập đúng đơn giá độ dày hoặc QS phê duyệt.",
            )

        if source == "AI quote memory composite":
            add_issue(
                "composite_memory",
                "Bắt buộc",
                line_label,
                "AI đang ghép giá từ nhiều dòng báo giá cũ",
                f"{desc} | Cần QS xác nhận trước khi phát hành.",
            )

        if source == "RAG bộ nhớ AI":
            add_issue(
                "rag_memory",
                "Cần xác nhận",
                line_label,
                "AI áp giá từ báo giá tương tự",
                f"{desc} | Điểm tương đồng: {float(item.get('rag_similarity_score') or 0):.0%}.",
            )

        if source not in trusted_sources and item.get("category") not in no_size_required:
            if not item.get("width") and not item.get("diameter"):
                add_issue("missing_size", "Bắt buộc", line_label, "Thiếu kích thước", desc)

        for warning in item.get("warnings") or []:
            add_issue("line_warning", "Cần kiểm tra", line_label, "Cảnh báo dòng hàng", str(warning))

        if source == "Đối soát báo giá chuẩn":
            add_issue(
                "reconciliation",
                "Cần xác nhận",
                line_label,
                "Dòng điều chỉnh đối soát",
                f"{desc} | Giá trị: {float(item.get('subtotal') or 0):,.2f}",
            )

    for idx, warning in enumerate(warnings[:100], start=1):
        add_issue("global_warning", "Cần kiểm tra", f"Cảnh báo {idx}", "Cảnh báo chung", str(warning))

    deduped: Dict[str, Dict[str, Any]] = {}
    for issue in issues:
        deduped.setdefault(issue["issue_id"], issue)
    return list(deduped.values())


def _render_sales_checklist(
    items: List[Dict[str, Any]],
    warnings: List[str],
    price_overrides: Dict[Tuple[str, float], float],
) -> int:
    issues = _build_sales_checklist(items, warnings, price_overrides)
    saved = st.session_state.setdefault("sales_checklist_state", {})

    rows = []
    for issue in issues:
        state = saved.get(issue["issue_id"], {})
        row = dict(issue)
        row["Đã xử lý"] = bool(state.get("resolved", False))
        row["Ghi chú sales"] = state.get("note", "")
        rows.append(row)

    if not rows:
        st.session_state.sales_checklist_unresolved = 0
        st.success("Sales không còn mục bắt buộc cần xử lý trước khi xuất chính thức.")
        return 0

    st.caption("Sales phải tick Đã xử lý hoặc ghi chú các mục dưới đây. File chính thức bị chặn nếu còn mục chưa xử lý.")
    edited = st.data_editor(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        key="sales_checklist_editor",
        disabled=["Mức độ", "Dòng", "Vấn đề", "Chi tiết"],
        column_config={
            "issue_id": None,
            "Đã xử lý": st.column_config.CheckboxColumn("Đã xử lý"),
            "Ghi chú sales": st.column_config.TextColumn("Ghi chú sales"),
        },
    )

    unresolved = 0
    new_state = {}
    for _, row in edited.iterrows():
        issue_id = str(row["issue_id"])
        resolved = bool(row.get("Đã xử lý", False))
        note = str(row.get("Ghi chú sales", "") or "").strip()
        new_state[issue_id] = {"resolved": resolved, "note": note}
        if not resolved:
            unresolved += 1

    st.session_state.sales_checklist_state = new_state
    st.session_state.sales_checklist_unresolved = unresolved
    if unresolved:
        st.warning(f"Còn {unresolved} mục sales/QS chưa xử lý, chưa cho xuất báo giá chính thức.")
    else:
        st.success("Tất cả mục sales/QS đã được xác nhận.")
    return unresolved


def _reference_comparison_messages(
    calculated_items: List[Dict[str, Any]],
    reference_items: List[Dict[str, Any]],
) -> List[str]:
    if not reference_items:
        return []

    by_desc = {_reference_key(item): item for item in reference_items if item.get("reference_unit_price")}
    by_item_no = {
        str(item.get("item_no") or "").replace(".0", ""): item
        for item in reference_items
        if item.get("item_no") and item.get("reference_unit_price")
    }

    matched = 0
    old_subtotal = 0.0
    current_subtotal = 0.0
    high_variance = 0
    for item in calculated_items:
        item_no = str(item.get("item_no") or "").replace(".0", "")
        ref = by_item_no.get(item_no) or by_desc.get(_reference_key(item))
        if not ref:
            continue

        matched += 1
        qty = float(item.get("quantity") or 0)
        old_unit = float(ref.get("reference_unit_price") or 0)
        current_unit = float(item.get("quote_unit_price") or 0)
        old_subtotal += qty * old_unit
        current_subtotal += float(item.get("subtotal") or 0)
        if old_unit > 0 and abs(current_unit - old_unit) / old_unit >= 0.25:
            high_variance += 1

    if matched == 0:
        return ["Đã tải báo giá cũ để so sánh, nhưng chưa có dòng hiện tại nào khớp đơn giá báo giá cũ."]

    delta_pct = ((current_subtotal - old_subtotal) / old_subtotal) if old_subtotal else 0.0
    messages = [
        (
            "Đã tải báo giá cũ để so sánh. "
            f"Khớp {matched}/{len(calculated_items)} dòng; tổng phụ hiện tại chênh {delta_pct:+.1%} "
            "so với đơn giá bán cũ đã khớp."
        )
    ]
    if high_variance:
        messages.append(
            f"KIỂM TRA GIÁ: {high_variance} dòng khớp đang lệch từ 25% trở lên so với đơn giá cũ. "
            "Cần kiểm tra bảng giá/quy tắc phụ kiện trước khi phát hành cho khách hàng mới."
        )
    return messages


def _reference_subtotal(reference_items: List[Dict[str, Any]]) -> float:
    subtotal = 0.0
    for item in reference_items:
        line_total = item.get("reference_line_total")
        if line_total is None:
            qty = float(item.get("quantity") or 0)
            unit_price = float(item.get("reference_unit_price") or 0)
            line_total = qty * unit_price
        subtotal += float(line_total or 0)
    return round(subtotal, 2)


def _reference_match_count(calculated_items: List[Dict[str, Any]], reference_items: List[Dict[str, Any]]) -> int:
    by_desc = {_reference_key(item): item for item in reference_items if item.get("reference_unit_price")}
    by_item_no = {
        str(item.get("item_no") or "").replace(".0", ""): item
        for item in reference_items
        if item.get("item_no") and item.get("reference_unit_price")
    }
    matched = 0
    for item in calculated_items:
        item_no = str(item.get("item_no") or "").replace(".0", "")
        if by_item_no.get(item_no) or by_desc.get(_reference_key(item)):
            matched += 1
    return matched


def _apply_reference_total_reconciliation(
    calculated_items: List[Dict[str, Any]],
    summary: Dict[str, Any],
    reference_items: List[Dict[str, Any]],
    vat_rate: float,
) -> tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    if not calculated_items or not reference_items:
        return calculated_items, summary, []

    reference_total = _reference_subtotal(reference_items)
    if reference_total <= 0:
        return calculated_items, summary, []

    matched = _reference_match_count(calculated_items, reference_items)
    match_ratio = matched / max(len(calculated_items), 1)
    if match_ratio < 0.80:
        return calculated_items, summary, [
            f"Không đối soát tổng vì chỉ khớp {matched}/{len(calculated_items)} dòng với báo giá hoàn thành."
        ]

    current_total = round(float(summary.get("subtotal") or summary.get("items_subtotal") or 0), 2)
    delta = round(reference_total - current_total, 2)
    updated_items = list(calculated_items)
    messages = [
        f"Đối soát tổng theo báo giá hoàn thành: khớp {matched}/{len(calculated_items)} dòng; tổng chuẩn trước VAT {reference_total:,.2f}."
    ]

    if abs(delta) >= 1:
        updated_items.append({
            "item_no": "ĐC",
            "mark": "ADJ-001",
            "category": "ADJUSTMENT",
            "description": "Điều chỉnh đối soát theo báo giá đã hoàn thành",
            "quantity": 1,
            "unit": "lô",
            "quote_unit_price": delta,
            "reference_unit_price": delta,
            "reference_source": "Đối soát báo giá chuẩn",
            "calculated_area": 0.0,
            "material_cost": delta,
            "labor_cost": 0.0,
            "accessory_cost": 0.0,
            "installation_cost": 0.0,
            "painting_cost": 0.0,
            "insulation_cost": 0.0,
            "waste_cost": 0.0,
            "subtotal": delta,
            "profit": 0.0,
            "vat": 0.0,
            "grand_total": delta,
            "quote_material_spec": "Đối soát nội bộ",
            "quote_brand": "Kaiyo Việt Nam",
            "quote_note": f"Chênh lệch làm khớp tổng báo giá chuẩn: {delta:,.2f}",
            "warnings": [],
        })
        messages.append(f"Đã thêm dòng điều chỉnh đối soát {delta:+,.2f} trước VAT.")

    subtotal = round(reference_total, 2)
    vat = round(subtotal * vat_rate, 2)
    reconciled_summary = {
        **summary,
        "total_material": subtotal,
        "items_subtotal": subtotal,
        "transportation": 0.0,
        "machinery": 0.0,
        "management_fee": 0.0,
        "risk_fee": 0.0,
        "subtotal": subtotal,
        "profit": 0.0,
        "vat": vat,
        "grand_total": round(subtotal + vat, 2),
        "reference_reconciled": True,
        "reference_subtotal": subtotal,
        "reference_adjustment": delta,
        "reference_matched": matched,
    }
    return updated_items, reconciled_summary, messages


def _admin_table_editor(table: str, key_columns: List[str]) -> None:
    conn = get_db_connection()
    frame = pd.read_sql_query(f"SELECT * FROM {table}", conn)
    conn.close()
    edited = st.data_editor(frame, use_container_width=True, num_rows="dynamic", key=f"admin_{table}")
    if st.button(f"Lưu {table}", key=f"save_{table}"):
        conn = get_db_connection()
        conn.execute(f"DELETE FROM {table}")
        edited.to_sql(table, conn, if_exists="append", index=False)
        conn.commit()
        conn.close()
        st.success(f"Đã lưu {table}.")


st.title("Hệ thống báo giá HVAC AI")
st.caption("AI hỗ trợ đọc khối lượng, học đơn giá báo giá cũ, quản lý bảng giá, kiểm tra sai lệch và xuất Excel/PDF.")

with st.sidebar:
    st.subheader("Thông tin dự án")
    customer = st.text_input("Khách hàng", value=st.session_state.get("customer", ""))
    project = st.text_input("Dự án", value=st.session_state.get("project", ""))
    st.session_state.customer = customer
    st.session_state.project = project

    material_file = st.file_uploader("File khối lượng khách gửi", type=["xlsx", "xls", "png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"])
    template_file = st.file_uploader("Mẫu báo giá Excel", type=["xlsx", "xls"])
    if template_file:
        template_name = template_file.name
        if template_name.lower().endswith(".xls") and not template_name.lower().endswith(".xlsx"):
            st.session_state.template_bytes = None
            st.session_state.template_name = ""
            st.warning("Mẫu báo giá dạng .xls chưa thể giữ nguyên form khi xuất. Hãy lưu mẫu sang .xlsx rồi tải lại.")
        else:
            st.session_state.template_bytes = template_file.getvalue()
            st.session_state.template_name = template_name
    material_file_looks_like_quote = bool(material_file and _looks_like_completed_quote_file(material_file.name))
    if material_file_looks_like_quote:
        st.warning(
            f"File '{material_file.name}' đang nằm ở ô khối lượng có vẻ là file báo giá đã hoàn thành. "
            "Ô này cần file KL/BOQ khách gửi; file báo giá đã hoàn thành hãy tải ở mục học/so sánh bên dưới."
        )
    if material_file and Path(material_file.name).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        st.info(
            "File khối lượng là ảnh, app sẽ dùng AI OCR để đọc. "
            "Để báo giá giống nhân viên đã làm, hãy tải thêm file báo giá hoàn thành ở mục học/so sánh bên dưới."
        )
    with st.expander("Tùy chọn: học/so sánh với báo giá đã làm", expanded=not bool(st.session_state.get("reference_quote_items"))):
        old_quote_file = st.file_uploader("File báo giá đã hoàn thành", type=["xlsx", "xls"])
        st.caption("Tải file báo giá cũ/chuẩn để AI học đơn giá bán thực tế hoặc so sánh sai lệch.")

    price_list_id = _select_price_list()
    st.session_state.price_list_id = price_list_id

    analyze_takeoff_clicked = st.button(
        "Phân tích khối lượng",
        type="primary",
        use_container_width=True,
        disabled=material_file is None,
    )
    if analyze_takeoff_clicked:
        try:
            if material_file is None:
                st.warning("Chưa có file KL/BOQ khách gửi. Hãy tải file trước khi phân tích.")
                st.stop()
            if material_file_looks_like_quote:
                st.error(
                    f"Không phân tích file '{material_file.name}' như KL khách gửi vì tên file giống báo giá đã hoàn thành. "
                    "Hãy tải file KL/BOQ khách gửi vào ô này, còn báo giá đã làm tải ở mục học/so sánh."
                )
                st.stop()
            _upload_to_storage(material_file, "customer_takeoff")
            if template_file:
                _upload_to_storage(template_file, "quote_template")
            st.session_state["takeoff_items"] = _parse_uploaded(material_file)
            st.session_state.file_name = material_file.name
            st.session_state.manual_unit_prices = {}
            st.success(f"Đã đọc {len(st.session_state['takeoff_items'])} dòng sản phẩm.")
        except Exception as exc:
            st.error(f"Không thể đọc file: {type(exc).__name__}: {exc}")
            with st.expander("Chi tiết kỹ thuật"):
                st.exception(exc)

    if old_quote_file and st.button("Phân tích báo giá đã hoàn thành", use_container_width=True):
        try:
            _upload_to_storage(old_quote_file, "completed_quote")
            st.session_state.old_items = _parse_uploaded(old_quote_file)
            st.session_state.reference_quote_items = _parse_reference_uploaded(old_quote_file)
            memory_source_file = getattr(old_quote_file, "name", "") or "completed_quote.xlsx"
            trained = train_quote_memory(
                st.session_state.reference_quote_items,
                source_project=project,
                source_customer=customer,
                source_file=memory_source_file,
            )
            st.session_state.quote_memory_source_file = memory_source_file
            st.success(
                f"Đã tải {len(st.session_state.old_items)} dòng khối lượng cũ và "
                f"{len(st.session_state.reference_quote_items)} dòng đơn giá báo giá. AI đã học {trained} dòng."
            )
        except Exception as exc:
            st.error(f"Không thể đọc file báo giá: {type(exc).__name__}: {exc}")
            with st.expander("Chi tiết kỹ thuật"):
                st.exception(exc)
    elif old_quote_file and not st.session_state.get("reference_quote_items"):
        st.info("Bấm Phân tích báo giá đã hoàn thành để tải đơn giá trước khi ước tính.")

    if st.session_state.get("reference_quote_items") and st.button("Cho AI học từ báo giá này", use_container_width=True):
        memory_source_file = getattr(old_quote_file, "name", "") or "completed_quote.xlsx"
        trained = train_quote_memory(
            st.session_state.reference_quote_items,
            source_project=project,
            source_customer=customer,
            source_file=memory_source_file,
        )
        st.session_state.quote_memory_source_file = memory_source_file
        st.success(f"AI đã học {trained} dòng đơn giá bán từ báo giá hoàn thành.")

if "takeoff_items" not in st.session_state:
    _render_bulk_completed_quote_import("startup")
    if st.session_state.get("reference_quote_items"):
        st.warning(
            "Bạn đã phân tích/học file báo giá đã hoàn thành, nhưng chưa phân tích file KL/BOQ khách gửi. "
            "Hãy tải đúng file KL/BOQ vào ô File khối lượng khách gửi rồi bấm Phân tích khối lượng."
        )
    elif material_file and _looks_like_completed_quote_file(material_file.name):
        st.warning(
            "File đang tải ở ô khối lượng giống báo giá đã hoàn thành. "
            "Để AI báo giá đúng, ô này phải là file KL/BOQ khách gửi."
        )
    else:
        st.info("Tải file khối lượng khách gửi rồi bấm Phân tích khối lượng.")
    st.stop()

items = st.session_state["takeoff_items"]

tab_agent, tab_sales_bot, tab_est, tab_compare, tab_explain, tab_memory, tab_prices, tab_sessions, tab_admin, tab_export = st.tabs(
    ["Quy trình AI", "Trợ lý sales/QS", "Ước tính", "So sánh", "Giải thích", "Bộ nhớ AI", "Đơn giá", "Phiên báo giá", "Quản trị", "Xuất file"]
)

with tab_est:
    st.subheader("Thông tin tính giá")
    quote_mode = st.radio(
        "Chế độ báo giá",
        ["Báo giá khách mới", "Báo giá sửa đổi cùng dự án"],
        horizontal=True,
        help="Báo giá khách mới dùng bảng giá/quy tắc hiện hành. Báo giá sửa đổi có thể dùng lại đơn giá cũ sau khi kiểm tra.",
    )
    st.session_state.quote_mode = quote_mode
    use_reference_prices = False
    if st.session_state.get("reference_quote_items") and quote_mode == "Báo giá sửa đổi cùng dự án":
        use_reference_prices = st.checkbox(
            "Áp đơn giá báo giá cũ cho các dòng khớp",
            value=False,
            help="Chỉ dùng khi đây là báo giá sửa đổi cùng dự án/phạm vi và đã được kiểm tra.",
        )
    elif st.session_state.get("reference_quote_items"):
        st.info("Đơn giá báo giá cũ chỉ dùng để so sánh/kiểm tra, không tự ghi đè báo giá khách mới.")
    overrides = _coefficient_editor()
    price_overrides = _price_editor(items, st.session_state.get("price_list_id"))
    st.session_state.overrides = overrides
    st.session_state.price_overrides = price_overrides

    calculated_items, summary, warnings = run_project_pricing(
        items,
        overrides=overrides,
        price_overrides=price_overrides,
        quote_memory_source_file=st.session_state.get("quote_memory_source_file", ""),
    )
    reference_messages: List[str] = []
    if use_reference_prices:
        settings_for_vat = get_company_settings()
        calculated_items, summary, ref_warnings = _apply_reference_prices(
            calculated_items,
            st.session_state.get("reference_quote_items", []),
            float(settings_for_vat.get("default_vat_pct", "0.10") or 0.10),
        )
        reference_messages = ref_warnings
        warnings = _filter_reference_priced_warnings(warnings, calculated_items)
    elif st.session_state.get("reference_quote_items"):
        reference_messages = _reference_comparison_messages(
            calculated_items,
            st.session_state.get("reference_quote_items", []),
        )
        warnings.extend(message for message in reference_messages if message.startswith("KIỂM TRA GIÁ:"))
    reconcile_with_reference = False
    if st.session_state.get("reference_quote_items"):
        reconcile_with_reference = st.checkbox(
            "Đối soát tổng theo báo giá đã hoàn thành",
            value=True,
            help="Bật khi file báo giá đã hoàn thành là bản chuẩn cần khớp 100% tổng trước VAT/sau VAT.",
        )
    with st.expander("Nhập đơn giá bán theo từng dòng", expanded=not bool(summary.get("quote_memory_matched"))):
        st.caption("Dùng phần này khi file mới chưa có bộ nhớ báo giá hoặc sales muốn chỉnh đơn giá bán trực tiếp.")
        manual_prices = _manual_price_editor(calculated_items)
    if manual_prices:
        settings_for_vat = get_company_settings()
        calculated_items, summary, manual_messages = _apply_manual_unit_prices(
            calculated_items,
            manual_prices,
            float(settings_for_vat.get("default_vat_pct", "0.10") or 0.10),
        )
        reference_messages = reference_messages + manual_messages
    if reconcile_with_reference:
        settings_for_vat = get_company_settings()
        calculated_items, summary, reconcile_messages = _apply_reference_total_reconciliation(
            calculated_items,
            summary,
            st.session_state.get("reference_quote_items", []),
            float(settings_for_vat.get("default_vat_pct", "0.10") or 0.10),
        )
        reference_messages = reference_messages + reconcile_messages
    if not summary.get("quote_memory_matched") and not manual_prices and not st.session_state.get("reference_quote_items"):
        warnings.append("Chưa có đơn giá bán chuẩn cho file mới. Hãy nhập đơn giá theo từng dòng hoặc cho AI học từ báo giá đã hoàn thành.")
    warnings = _missing_price_messages(calculated_items, price_overrides) + warnings
    settings = get_company_settings()
    readiness = evaluate_quote_readiness(calculated_items, summary, warnings, price_overrides, settings)
    st.session_state.calculated_items = calculated_items
    st.session_state.summary = summary
    st.session_state.warnings = warnings
    st.session_state.reference_messages = reference_messages
    st.session_state.readiness = readiness
    st.session_state.company_settings = settings

    for message in reference_messages:
        st.info(message)
    if summary.get("quote_memory_matched"):
        st.success(f"Bộ nhớ AI đã tự báo giá {int(summary.get('quote_memory_matched', 0))}/{len(calculated_items)} dòng.")
    with st.expander("Chẩn đoán tính giá", expanded=False):
        st.write({
            "dòng_khối_lượng": len(items),
            "dòng_báo_giá_đã_tải": len(st.session_state.get("reference_quote_items", [])),
            "ai_quote_memory_khớp": int(summary.get("quote_memory_matched", 0) or 0),
            "sản_phẩm_chưa_xác_định": readiness["unknown_count"],
            "thiếu_giá": readiness["missing_price_count"],
            "thiếu_size": readiness["missing_size_count"],
            "tổng_cộng": summary.get("grand_total", 0),
        })
        if st.session_state.get("reference_quote_items") and not summary.get("quote_memory_matched"):
            st.warning(
                "Đã tải dòng báo giá hoàn thành nhưng chưa áp được bộ nhớ AI. "
                "Bấm Cho AI học từ báo giá này, sau đó chạy lại Phân tích khối lượng."
            )
    if not st.session_state.get("reference_quote_items"):
        st.warning(
            "Chưa có báo giá hoàn thành được tải để học/so sánh. Với báo giá khách mới, hãy hoàn thiện bảng giá "
            "và quy tắc sản phẩm thay vì sao chép giá dự án cũ."
        )
    if warnings:
        st.markdown("<span class='status-warn'>Cần kiểm tra trước khi phát hành.</span>", unsafe_allow_html=True)
        for warning in warnings[:10]:
            st.warning(warning)
    else:
        st.markdown("<span class='status-ok'>Đủ điều kiện xuất báo giá.</span>", unsafe_allow_html=True)

    _show_metrics(summary)
    sub_items, sub_groups = st.tabs(["Dòng sản phẩm", "Tổng hợp nhóm"])
    with sub_items:
        st.dataframe(_items_to_frame(calculated_items), use_container_width=True, hide_index=True)
    with sub_groups:
        st.dataframe(_group_items(calculated_items), use_container_width=True, hide_index=True)

with tab_agent:
    st.subheader("Quy trình kiểm tra báo giá AI")
    calculated_items = st.session_state.get("calculated_items", [])
    summary = st.session_state.get("summary", {})
    warnings = st.session_state.get("warnings", [])
    settings = st.session_state.get("company_settings", get_company_settings())
    readiness = st.session_state.get("readiness") or evaluate_quote_readiness(
        calculated_items,
        summary,
        warnings,
        st.session_state.get("price_overrides", {}),
        settings,
    )
    st.session_state.readiness = readiness

    status_class = "status-ok" if readiness["status"] == "READY" else "status-warn" if readiness["status"] == "NEEDS_INPUT" else "status-block"
    st.markdown(
        f"<span class='{status_class}'>Trạng thái: {_vi_status(readiness['status'])} | Điểm sẵn sàng: {readiness['score']}/100</span>",
        unsafe_allow_html=True,
    )
    for message in st.session_state.get("reference_messages", []):
        st.info(message)
    if summary.get("quote_memory_matched"):
        st.success(f"Bộ nhớ AI đã tự báo giá {int(summary.get('quote_memory_matched', 0))}/{len(calculated_items)} dòng.")
    with st.expander("Chẩn đoán tính giá", expanded=True):
        st.write({
            "dòng_đã_tính": len(calculated_items),
            "dòng_báo_giá_đã_tải": len(st.session_state.get("reference_quote_items", [])),
            "dòng_khớp_bộ_nhớ_AI": int(summary.get("quote_memory_matched", 0) or 0),
            "sản_phẩm_chưa_xác_định": readiness["unknown_count"],
            "thiếu_giá": readiness["missing_price_count"],
            "thiếu_kích_thước": readiness["missing_size_count"],
            "tổng_cộng": summary.get("grand_total", 0),
        })
        if st.session_state.get("reference_quote_items") and not summary.get("quote_memory_matched"):
            st.warning(
                "Đã tải báo giá hoàn thành nhưng chưa áp được bộ nhớ giá AI. "
                "Bấm Cho AI học từ báo giá này, sau đó chạy lại Phân tích khối lượng."
            )
    if not st.session_state.get("reference_quote_items"):
        st.warning(
            "Chưa tải báo giá hoàn thành để học/so sánh. Nếu là yêu cầu mới, cần hoàn thiện bảng giá/quy tắc hiện hành. "
            "Nếu là báo giá sửa đổi, hãy tải báo giá cũ để so sánh và phê duyệt."
        )
    _show_metrics(summary)

    col_a, col_b, col_c, col_d = st.columns(4)
    col_a.metric("Chưa xác định", readiness["unknown_count"])
    col_b.metric("Thiếu giá", readiness["missing_price_count"])
    col_c.metric("Sai số lượng", readiness["invalid_qty_count"])
    col_d.metric("Thiếu size", readiness["missing_size_count"])

    st.subheader("Câu hỏi AI")
    if readiness["questions"]:
        for question in readiness["questions"]:
            st.warning(question)
    else:
        st.success("Không thiếu câu hỏi kỹ thuật hoặc thương mại theo chính sách hiện tại.")

    st.subheader("Việc cần xử lý")
    if readiness["actions"]:
        for action in readiness["actions"]:
            st.info(action)
    else:
        st.success("Không cần xử lý thêm trước khi phê duyệt.")

    st.subheader("Checklist bắt buộc cho sales/QS")
    _render_sales_checklist(
        calculated_items,
        warnings,
        st.session_state.get("price_overrides", {}),
    )

    with st.expander("Tóm tắt nội bộ cho người duyệt", expanded=True):
        st.code(estimator_brief(calculated_items, summary, warnings, readiness, settings), language="text")

    with st.expander("Phê duyệt"):
        approver = st.text_input("Người duyệt", value="")
        approval_status_options = {
            "Bản nháp": "DRAFT",
            "Cần sửa": "NEEDS_REVISION",
            "Đã duyệt": "APPROVED",
            "Từ chối": "REJECTED",
        }
        approval_status_label = st.selectbox("Trạng thái phê duyệt", list(approval_status_options))
        approval_status = approval_status_options[approval_status_label]
        approval_notes = st.text_area("Ghi chú phê duyệt")
        if st.button("Lưu quyết định phê duyệt"):
            approval_id = save_approval(
                approval_status,
                project=st.session_state.get("project", ""),
                customer=st.session_state.get("customer", ""),
                approver=approver,
                notes=approval_notes,
                readiness_score=readiness["score"],
            )
            st.success(f"Đã lưu phê duyệt #{approval_id}.")
        approvals = list_approvals()
        st.dataframe(pd.DataFrame(approvals), use_container_width=True, hide_index=True)

with tab_sales_bot:
    _render_sales_qs_chatbot(st.session_state.get("calculated_items", []))

with tab_compare:
    st.subheader("So sánh khối lượng hiện tại với báo giá cũ")
    if "old_items" not in st.session_state:
        st.info("Tải báo giá cũ ở thanh bên, sau đó bấm Phân tích báo giá đã hoàn thành.")
    else:
        diff_rows = compare_items(st.session_state.calculated_items, st.session_state.old_items)
        st.dataframe(pd.DataFrame(diff_rows), use_container_width=True, hide_index=True)

with tab_explain:
    st.subheader("Giải thích cách tính từng dòng")
    calculated_items = st.session_state.get("calculated_items", [])
    if not calculated_items:
        st.info("Hãy chạy phân tích/ước tính trước.")
    else:
        labels = [f"{idx + 1}. {item.get('mark')} - {item.get('description')}" for idx, item in enumerate(calculated_items)]
        chosen = st.selectbox("Chọn dòng", labels)
        idx = labels.index(chosen)
        item = calculated_items[idx]
        key = (item.get("material", "GI"), float(item.get("thickness") or 0))
        unit_price = st.session_state.get("price_overrides", {}).get(key, 0.0)
        st.code(explain_item(item, st.session_state.get("overrides", {}), unit_price), language="text")

with tab_memory:
    _render_bulk_completed_quote_import("memory")

    st.subheader("AI học từ báo giá hiện tại")
    st.write("Khi lưu học, đơn giá và hệ số hiện tại sẽ trở thành gợi ý cho các dự án tương tự sau này.")
    if st.button("Học từ báo giá hiện tại", type="primary"):
        count = learn_from_quote(
            st.session_state.get("calculated_items", []),
            st.session_state.get("overrides", {}),
            st.session_state.get("price_overrides", {}),
            source_project=project,
            source_customer=customer,
        )
        st.success(f"Đã học {count} mẫu giá sản phẩm.")
    suggestions = learned_suggestions(st.session_state.get("calculated_items", []))
    st.dataframe(pd.DataFrame(list(suggestions.values())), use_container_width=True, hide_index=True)

    st.subheader("Bộ nhớ báo giá hoàn thành")
    st.caption("Đây là đơn giá bán đã học từ các file báo giá hoàn thành.")
    st.dataframe(pd.DataFrame(list_quote_memory()), use_container_width=True, hide_index=True)

with tab_prices:
    st.subheader("Bảng giá và nơi nhập đơn giá")
    st.info("Sales có thể nhập nhanh đơn giá vật liệu trong tab Ước tính. Muốn lưu thành bảng giá dùng các nút bên dưới.")
    st.dataframe(pd.DataFrame(list_price_lists()), use_container_width=True, hide_index=True)
    with st.expander("Tạo bảng giá mới"):
        new_name = st.text_input("Tên bảng giá", key="new_price_list_name")
        new_supplier = st.text_input("Nhà cung cấp", key="new_price_list_supplier")
        new_region = st.text_input("Khu vực", key="new_price_list_region")
        new_customer = st.text_input("Khách hàng", key="new_price_list_customer")
        if st.button("Tạo bảng giá") and new_name:
            create_price_list(new_name, new_supplier, new_region, new_customer)
            st.success("Đã tạo bảng giá. Rerun để chọn bảng giá mới.")
    with st.expander("Lưu các đơn giá đang nhập vào bảng giá đang chọn"):
        if st.button("Lưu đơn giá hiện tại"):
            selected_price_list = st.session_state.get("price_list_id")
            if not selected_price_list:
                st.error("Chưa chọn bảng giá.")
            else:
                for (material, thickness), unit_price in st.session_state.get("price_overrides", {}).items():
                    upsert_price_list_item(selected_price_list, material, thickness, unit_price, source="Báo giá hiện tại")
                st.success("Đã lưu đơn giá vào bảng giá đang chọn.")

with tab_sessions:
    st.subheader("Lịch sử phiên báo giá")
    session_name = st.text_input("Tên phiên", value=f"{project or 'Báo giá HVAC'} - {customer or 'Khách hàng'}")
    if st.button("Lưu phiên hiện tại", type="primary"):
        session_id = save_quotation_session(
            session_name,
            st.session_state.get("calculated_items", []),
            st.session_state.get("summary", {}),
            st.session_state.get("overrides", {}),
            st.session_state.get("price_overrides", {}),
            customer=customer,
            project=project,
            source_file=st.session_state.get("file_name", ""),
            price_list_id=st.session_state.get("price_list_id"),
        )
        st.success(f"Đã lưu phiên #{session_id}.")
    sessions = list_quotation_sessions()
    st.dataframe(pd.DataFrame(sessions), use_container_width=True, hide_index=True)
    if sessions:
        session_map = {f"#{row['id']} {row['name']}": row["id"] for row in sessions}
        chosen_session = st.selectbox("Mở phiên đã lưu", list(session_map))
        if st.button("Tải phiên"):
            data = load_quotation_session(session_map[chosen_session])
            if data:
                st.session_state["takeoff_items"] = data["items"]
                st.session_state.calculated_items = data["items"]
                st.session_state.summary = data["summary"]
                st.session_state.overrides = data["coefficients"]
                st.session_state.price_overrides = data["price_overrides"]
                st.success("Đã tải phiên. Mở tab Ước tính để tiếp tục chỉnh sửa.")

with tab_admin:
    st.subheader("Quản trị quy tắc")
    using_supabase = supabase_store.enabled()
    if using_supabase:
        st.info(
            "Ứng dụng đang chạy bằng Supabase/PostgreSQL ở chế độ production. "
            "Bảng giá, quy tắc sản phẩm, alias, bộ nhớ báo giá, phiên và phê duyệt đều dùng Supabase."
        )
    else:
        st.warning("Các chỉnh sửa này ghi trực tiếp vào cơ sở dữ liệu quy tắc SQLite.")
    admin_tabs = st.tabs(["Chính sách công ty", "Giá sản phẩm", "Độ dày", "Hệ số", "Tiền tố ký hiệu", "Danh mục"])
    with admin_tabs[0]:
        settings_rows = list_company_settings()
        settings_frame = pd.DataFrame(settings_rows)
        edited_settings = st.data_editor(settings_frame, use_container_width=True, num_rows="dynamic", key="company_settings_editor")
        if st.button("Lưu chính sách công ty"):
            for _, row in edited_settings.iterrows():
                update_company_setting(str(row["key"]), str(row["value"]), str(row.get("description", "")))
            st.success("Đã lưu chính sách công ty.")
    with admin_tabs[1]:
        if using_supabase:
            st.warning("Rule sản phẩm đang nằm trên Supabase. Hãy chỉnh bảng product_pricing_rules/product_categories trên Supabase hoặc xây thêm màn hình admin riêng.")
        else:
            _admin_table_editor("product_pricing_rules", ["category"])
    with admin_tabs[2]:
        if using_supabase:
            st.warning("Rule độ dày đang nằm trên Supabase. Hãy chỉnh thickness_rules trên Supabase.")
        else:
            _admin_table_editor("thickness_rules", ["id"])
    with admin_tabs[3]:
        if using_supabase:
            st.warning("Hệ số tính giá đang đọc từ Supabase. Hãy sửa coefficient_rules trên Supabase.")
        else:
            _admin_table_editor("coefficient_rules", ["key"])
    with admin_tabs[4]:
        if using_supabase:
            st.warning("Tiền tố ký hiệu hiện dùng cấu hình seed runtime trong app. Nếu muốn quản trị động, cần thêm bảng mark_prefix_rules trên Supabase.")
        else:
            _admin_table_editor("mark_rules", ["category"])
    with admin_tabs[5]:
        if using_supabase:
            st.warning("Danh mục sản phẩm/alias đang đọc từ Supabase. Hãy sửa product_categories, product_master, product_aliases trên Supabase.")
        else:
            _admin_table_editor("product_categories", ["normalized_key"])

with tab_export:
    st.subheader("Xuất file")
    calculated_items = st.session_state.get("calculated_items", [])
    summary = st.session_state.get("summary", {})
    settings = st.session_state.get("company_settings", get_company_settings())
    readiness = st.session_state.get("readiness") or {}
    approved = has_final_approval(st.session_state.get("project", ""), st.session_state.get("customer", ""))
    approval_required = settings.get("require_human_approval", "1") == "1"
    no_calculated_items = not calculated_items
    unresolved_checklist = int(st.session_state.get("sales_checklist_unresolved", 0) or 0)
    partial_memory_match = bool(summary.get("quote_memory_partial_match"))
    export_blocked = (
        no_calculated_items
        or readiness.get("status") != "READY"
        or unresolved_checklist > 0
        or partial_memory_match
        or (approval_required and not approved)
    )
    if no_calculated_items:
        st.error("Chưa có dòng báo giá để xuất. Hãy bấm Phân tích khối lượng và kiểm tra tab Ước tính trước.")
    if readiness.get("status") != "READY":
        st.error("Bị chặn xuất chính thức cho đến khi trạng thái AI là SẴN SÀNG.")
    if unresolved_checklist > 0:
        st.error(f"Bị chặn xuất chính thức vì còn {unresolved_checklist} mục checklist sales/QS chưa xử lý.")
    if partial_memory_match:
        st.error("Bị chặn xuất chính thức vì bộ nhớ AI chỉ khớp một phần dòng báo giá. QS cần kiểm tra và bổ sung giá cho các dòng còn lại.")
    if approval_required and not approved:
        st.error("Bị chặn xuất chính thức cho đến khi có phê duyệt ĐÃ DUYỆT.")
    if st.session_state.get("template_bytes"):
        st.info(f"File xuất sẽ ghi vào mẫu báo giá: {st.session_state.get('template_name', 'mẫu đã tải')}.")
    else:
        st.warning(
            "Chưa có mẫu báo giá .xlsx trong phiên hiện tại, nên Excel nháp sẽ dùng bảng kiểm tra mặc định. "
            "Muốn ra đúng form Kaiyo, hãy tải mẫu báo giá .xlsx ở thanh bên trước khi xuất."
        )

    signer = st.text_input("Chữ ký / người phê duyệt", value="Đã duyệt bởi người có thẩm quyền")
    quotation_bytes = build_quotation_workbook(
        calculated_items,
        summary,
        template_bytes=st.session_state.get("template_bytes"),
        project=st.session_state.get("project", ""),
        customer=st.session_state.get("customer", ""),
    )
    st.download_button(
        "Tải báo giá Excel chính thức",
        data=quotation_bytes,
        file_name="Quotation.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        disabled=export_blocked,
    )
    st.download_button(
        "Tải Excel nháp để kiểm tra",
        data=quotation_bytes,
        file_name="Quotation-DRAFT.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        disabled=False,
    )
    pdf_bytes = build_simple_pdf("Báo giá HVAC AI", calculated_items, summary, signer=signer)
    st.download_button("Tải PDF tóm tắt", data=pdf_bytes, file_name="Quotation.pdf", mime="application/pdf", disabled=export_blocked)
    audit_report = build_audit_report(calculated_items, summary, st.session_state.get("readiness", {}), settings)
    st.download_button("Tải biên bản kiểm tra", data=audit_report.encode("utf-8"), file_name="quotation_audit.md", mime="text/markdown")
    if supabase_store.enabled() and st.button("Lưu file xuất lên Supabase", use_container_width=True):
        try:
            output_kind = "generated_final" if not export_blocked else "generated_draft"
            excel_result = supabase_store.upload_project_file(
                "Quotation.xlsx" if not export_blocked else "Quotation-DRAFT.xlsx",
                quotation_bytes,
                output_kind,
                project=st.session_state.get("project", ""),
                customer=st.session_state.get("customer", ""),
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            pdf_result = supabase_store.upload_project_file(
                "Quotation.pdf",
                pdf_bytes,
                output_kind,
                project=st.session_state.get("project", ""),
                customer=st.session_state.get("customer", ""),
                mime_type="application/pdf",
            )
            audit_result = supabase_store.upload_project_file(
                "quotation_audit.md",
                audit_report.encode("utf-8"),
                "other",
                project=st.session_state.get("project", ""),
                customer=st.session_state.get("customer", ""),
                mime_type="text/markdown",
            )
            st.success(
                "Đã lưu file xuất lên Supabase Storage: "
                f"{excel_result.get('file_name')}, {pdf_result.get('file_name')}, {audit_result.get('file_name')}."
            )
        except Exception as exc:
            st.error(f"Không thể lưu file xuất lên Supabase Storage: {type(exc).__name__}: {exc}")
    st.markdown("<div class='small-note'>Chữ ký ở đây là khối phê duyệt nội bộ, chưa phải chứng thư số mã hóa.</div>", unsafe_allow_html=True)
