from __future__ import annotations

import os
from io import BytesIO
from copy import copy
from typing import Any, Dict, List, Optional

import openpyxl
from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


EXPORT_COLUMNS = [
    ("item_no", "STT"),
    ("mark", "Mark"),
    ("category", "Nhóm sản phẩm"),
    ("description", "Tên vật tư"),
    ("width", "Rộng"),
    ("height", "Cao"),
    ("diameter", "Đường kính"),
    ("length", "Dài"),
    ("thickness", "Độ dày"),
    ("material", "Vật liệu"),
    ("quantity", "Khối lượng"),
    ("unit", "Đơn vị"),
    ("quote_unit_price", "Đơn giá bán"),
    ("reference_source", "Nguồn giá"),
    ("calculated_area", "Diện tích m2"),
    ("material_cost", "Tiền vật liệu"),
    ("labor_cost", "Nhân công"),
    ("accessory_cost", "Phụ kiện"),
    ("installation_cost", "Lắp đặt"),
    ("painting_cost", "Sơn"),
    ("insulation_cost", "Bảo ôn"),
    ("waste_cost", "Hao hụt"),
    ("subtotal", "Tổng phụ"),
    ("profit", "Lợi nhuận"),
    ("vat", "VAT"),
    ("grand_total", "Tổng cộng"),
    ("warnings", "Cảnh báo"),
]


SUMMARY_COLUMNS = [
    ("total_area", "Tổng diện tích"),
    ("total_material", "Vật liệu"),
    ("total_labor", "Nhân công"),
    ("total_accessory", "Phụ kiện"),
    ("total_installation", "Lắp đặt"),
    ("total_painting", "Sơn"),
    ("total_insulation", "Bảo ôn"),
    ("total_waste", "Hao hụt"),
    ("items_subtotal", "Tổng dòng hàng"),
    ("transportation", "Vận chuyển"),
    ("machinery", "Máy móc"),
    ("management_fee", "Phí quản lý"),
    ("risk_fee", "Phí rủi ro"),
    ("subtotal", "Tổng phụ"),
    ("profit", "Lợi nhuận"),
    ("vat", "VAT"),
    ("grand_total", "Tổng cộng"),
]


def _product_only_export() -> bool:
    return os.getenv("PRODUCT_ONLY_MODE", "1").strip().lower() not in {"0", "false", "no", "off"}


def _export_money(value: Any) -> Any:
    return None if _product_only_export() else value


PRODUCT_EXPORT_COLUMNS = [
    ("item_no", "STT"),
    ("mark", "Mark"),
    ("category", "Nhóm sản phẩm"),
    ("description", "Tên vật tư"),
    ("width", "Rộng"),
    ("height", "Cao"),
    ("diameter", "Đường kính"),
    ("length", "Dài"),
    ("thickness", "Độ dày"),
    ("material", "Vật liệu"),
    ("quantity", "Khối lượng"),
    ("unit", "Đơn vị"),
    ("calculated_area", "Diện tích m2"),
    ("quote_code", "Mã công thức"),
    ("quote_material_spec", "Vật liệu chế tạo"),
    ("quote_brand", "Xuất xứ"),
    ("quote_note", "Ghi chú"),
    ("warnings", "Cảnh báo"),
]

PRODUCT_SUMMARY_COLUMNS = [
    ("total_area", "Tổng diện tích"),
    ("item_count", "Số dòng sản phẩm"),
]


def _export_columns() -> List[tuple[str, str]]:
    return PRODUCT_EXPORT_COLUMNS if _product_only_export() else EXPORT_COLUMNS


def _summary_columns() -> List[tuple[str, str]]:
    return PRODUCT_SUMMARY_COLUMNS if _product_only_export() else SUMMARY_COLUMNS


def build_quotation_workbook(
    items: List[Dict[str, Any]],
    summary: Dict[str, float],
    template_bytes: Optional[bytes] = None,
    project: str = "",
    customer: str = "",
) -> bytes:
    """
    Build an Excel quotation. If a company template is supplied, its workbook is
    loaded and preserved; placeholders are filled when present, and an
    output is written directly into the detected quotation form.
    """
    if template_bytes:
        workbook = load_workbook(BytesIO(template_bytes))
        _remove_sheet_if_exists(workbook, "AI Quotation Data")
        _remove_sheet_if_exists(workbook, "Dữ liệu báo giá AI")
        _fill_placeholders(workbook, summary)
        if _try_write_kaiyo_quote(workbook, items, summary, project=project, customer=customer):
            output = BytesIO()
            workbook.save(output)
            return output.getvalue()
        sheet = _replace_sheet(workbook, "Dữ liệu báo giá AI")
    else:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Báo giá"

    _write_items_sheet(sheet, items, summary)

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def _try_write_kaiyo_quote(
    workbook: Workbook,
    items: List[Dict[str, Any]],
    summary: Dict[str, float],
    project: str = "",
    customer: str = "",
) -> bool:
    candidates = _kaiyo_sheet_candidates(workbook)
    for sheet in candidates:
        if _try_write_kaiyo_quote_sheet(sheet, items, summary, project=project, customer=customer):
            return True
    return False


def _try_write_kaiyo_quote_sheet(
    sheet,
    items: List[Dict[str, Any]],
    summary: Dict[str, float],
    project: str = "",
    customer: str = "",
) -> bool:
    header_row = _find_kaiyo_header_row(sheet)
    if not header_row or not _is_kaiyo_quote_header(sheet, header_row):
        return False

    data_start = _find_kaiyo_data_start_row(sheet, header_row) or header_row + 3
    old_summary_row = _find_kaiyo_summary_start_row(sheet, data_start)
    if old_summary_row and _try_update_existing_kaiyo_quote(sheet, items, summary, data_start, old_summary_row):
        sheet.freeze_panes = f"A{data_start}"
        return True

    template_style = _snapshot_row_style(sheet, data_start)
    if old_summary_row:
        delete_count = min(sheet.max_row - data_start + 1, old_summary_row + 4 - data_start + 1)
        sheet.delete_rows(data_start, delete_count)
        sheet.insert_rows(data_start, len(items) + 3)
    else:
        notes_row = _find_kaiyo_notes_start_row(sheet, data_start)
        required_rows = len(items) + 3
        if notes_row:
            available_rows = max(0, notes_row - data_start)
            if available_rows < required_rows:
                sheet.insert_rows(notes_row, required_rows - available_rows)
            elif available_rows > required_rows:
                sheet.delete_rows(data_start + required_rows, available_rows - required_rows)
        else:
            sheet.insert_rows(data_start, required_rows)
    for row in range(data_start, data_start + len(items) + 3):
        _apply_row_style_snapshot(sheet, template_style, row)

    if project:
        _set_first_matching_cell(sheet, ["Dự án", "Du an"], 1, 18, project, offset_cols=1)
    if customer:
        _set_first_matching_cell(sheet, ["Kính Gửi", "Kinh Gui"], 1, 18, customer, offset_cols=2)

    for idx, item in enumerate(items, start=0):
        row = data_start + idx
        qty = float(item.get("quote_output_quantity") or item.get("quantity") or 0)
        unit = item.get("quote_output_unit") or item.get("unit")
        unit_price = 0.0 if _product_only_export() else float(
            item.get("quote_output_unit_price")
            or item.get("quote_unit_price")
            or (float(item.get("grand_total") or 0) / qty if qty else 0)
        )
        total = 0.0 if _product_only_export() else float(item.get("subtotal") or item.get("grand_total") or qty * unit_price)

        values = {
            "A": item.get("item_no") or idx + 1,
            "B": item.get("description"),
            "C": item.get("quote_material_spec") or item.get("material"),
            "D": item.get("quote_brand") or "Kaiyo Viet Nam",
            "E": unit,
            "F": qty,
            "G": _export_money(unit_price),
            "H": _export_money(total),
            "I": item.get("quote_note") or item.get("remark"),
            "K": item.get("description"),
            "L": item.get("quote_code") or _infer_template_quote_code(item),
            "M": item.get("formula_width") or item.get("width") or item.get("diameter"),
            "N": item.get("formula_height") or item.get("height") or item.get("diameter"),
            "O": item.get("formula_width2") or item.get("width2"),
            "P": item.get("formula_height2") or item.get("height2"),
            "Q": item.get("formula_width3") or item.get("width3"),
            "R": item.get("formula_height3") or item.get("height3"),
            "S": item.get("formula_length") or item.get("length"),
            "T": item.get("formula_radius") or item.get("radius") or item.get("diameter"),
            "U": item.get("formula_angle") or item.get("angle"),
            "V": item.get("formula_area") or _area_per_item(item, qty),
            "W": _export_money(item.get("formula_unit_price") or item.get("quote_unit_price")),
            "X": _export_money(item.get("area_multiplier")),
            "Y": item.get("accessory_area") or item.get("flange_price"),
            "Z": _export_money(item.get("accessory_unit_price") or item.get("accessory_cost")),
            "AA": _export_money(item.get("installation_cost")),
        }
        for col, value in values.items():
            _safe_set_cell(sheet, f"{col}{row}", value)

    total_row = data_start + len(items)
    sheet[f"B{total_row}"] = "TỔNG CỘNG"
    sheet[f"H{total_row}"] = _export_money(summary.get("items_subtotal", summary.get("subtotal", 0)))
    sheet[f"G{total_row}"] = ""
    sheet[f"F{total_row}"] = ""

    vat_row = total_row + 1
    sheet[f"B{vat_row}"] = "VAT"
    sheet[f"H{vat_row}"] = _export_money(summary.get("vat", 0))

    grand_row = total_row + 2
    sheet[f"B{grand_row}"] = "TỔNG THANH TOÁN"
    sheet[f"H{grand_row}"] = _export_money(summary.get("grand_total", 0))

    sheet.freeze_panes = f"A{data_start}"
    return True


def _try_update_existing_kaiyo_quote(
    sheet,
    items: List[Dict[str, Any]],
    summary: Dict[str, float],
    data_start: int,
    summary_row: int,
) -> bool:
    item_queues: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        key = _normalize(item.get("description"))
        if key:
            item_queues.setdefault(key, []).append(item)

    matched = 0
    for row in range(data_start, summary_row):
        description = sheet.cell(row=row, column=2).value
        key = _normalize(description)
        queue = item_queues.get(key)
        if not queue:
            continue
        item = queue.pop(0)
        matched += 1
        _write_kaiyo_item_row(sheet, row, item, matched)

    if matched == 0:
        return False

    _write_kaiyo_summary_rows(sheet, summary_row, summary)
    return True


def _write_kaiyo_item_row(sheet, row: int, item: Dict[str, Any], fallback_item_no: int) -> None:
    qty = float(item.get("quote_output_quantity") or item.get("quantity") or 0)
    unit = item.get("quote_output_unit") or item.get("unit")
    unit_price = 0.0 if _product_only_export() else float(
        item.get("quote_output_unit_price")
        or item.get("quote_unit_price")
        or (float(item.get("grand_total") or 0) / qty if qty else 0)
    )
    total = 0.0 if _product_only_export() else float(item.get("subtotal") or item.get("grand_total") or qty * unit_price)
    note = item.get("quote_note") or item.get("remark")

    values = {
        "A": item.get("item_no") or fallback_item_no,
        "B": item.get("description"),
        "C": item.get("quote_material_spec") or item.get("material"),
        "D": item.get("quote_brand") or "Kaiyo Viet Nam",
        "E": unit,
        "F": qty,
        "G": _export_money(unit_price if total else None),
        "H": _export_money(total),
        "I": note,
        "K": item.get("description"),
        "L": item.get("quote_code") or _infer_template_quote_code(item),
        "M": item.get("formula_width") or item.get("width") or item.get("diameter"),
        "N": item.get("formula_height") or item.get("height") or item.get("diameter"),
        "O": item.get("formula_width2") or item.get("width2"),
        "P": item.get("formula_height2") or item.get("height2"),
        "Q": item.get("formula_width3") or item.get("width3"),
        "R": item.get("formula_height3") or item.get("height3"),
        "S": item.get("formula_length") or item.get("length"),
        "T": item.get("formula_radius") or item.get("radius") or item.get("diameter"),
        "U": item.get("formula_angle") or item.get("angle"),
        "V": item.get("formula_area") or _area_per_item(item, float(item.get("quantity") or 0)),
        "W": _export_money(item.get("formula_unit_price") or item.get("quote_output_unit_price") or item.get("quote_unit_price")),
        "X": _export_money(item.get("area_multiplier")),
        "Y": item.get("accessory_area") or item.get("flange_price"),
        "Z": _export_money(item.get("accessory_unit_price") or item.get("accessory_cost")),
        "AA": _export_money(item.get("installation_cost")),
    }
    for col, value in values.items():
        _safe_set_cell(sheet, f"{col}{row}", value)


def _write_kaiyo_summary_rows(sheet, total_row: int, summary: Dict[str, float]) -> None:
    _safe_set_cell(sheet, f"B{total_row}", "Tổng cộng giá trước thuế")
    _safe_set_cell(sheet, f"H{total_row}", _export_money(summary.get("items_subtotal", summary.get("subtotal", 0))))

    vat_row = total_row + 1
    _safe_set_cell(sheet, f"B{vat_row}", "Thuế GTGT 10%")
    _safe_set_cell(sheet, f"H{vat_row}", _export_money(summary.get("vat", 0)))

    grand_row = total_row + 4 if _normalize(sheet[f"B{total_row + 4}"].value).startswith("tong cong") else total_row + 2
    _safe_set_cell(sheet, f"B{grand_row}", "Tổng cộng giá sau thuế")
    _safe_set_cell(sheet, f"H{grand_row}", _export_money(summary.get("grand_total", 0)))


def _find_sheet(workbook: Workbook, name: str):
    needle = _normalize(name)
    for sheet in workbook.worksheets:
        if _normalize(sheet.title) == needle:
            return sheet
    return None


def _kaiyo_sheet_candidates(workbook: Workbook):
    candidates = []
    for sheet in [_find_sheet(workbook, "báo giá"), _find_sheet(workbook, "bao gia"), _find_kaiyo_quote_sheet(workbook)]:
        if sheet and sheet not in candidates:
            candidates.append(sheet)
    for sheet in workbook.worksheets:
        if sheet not in candidates:
            candidates.append(sheet)
    return candidates


def _find_kaiyo_quote_sheet(workbook: Workbook):
    for sheet in workbook.worksheets:
        header_row = _find_kaiyo_header_row(sheet)
        if header_row and _is_kaiyo_quote_header(sheet, header_row):
            return sheet
    return None


def _safe_set_cell(sheet, coordinate: str, value: Any) -> None:
    cell = sheet[coordinate]
    if not isinstance(cell, MergedCell):
        cell.value = value
        return

    for merged_range in sheet.merged_cells.ranges:
        if coordinate in merged_range:
            top_left = sheet.cell(merged_range.min_row, merged_range.min_col)
            top_left.value = value
            return


def _normalize(text: Any) -> str:
    import unicodedata

    value = "" if text is None else str(text)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("đ", "d").replace("Đ", "D")
    return value.lower().strip()


def _find_kaiyo_header_row(sheet) -> Optional[int]:
    for row in range(1, min(sheet.max_row, 80) + 1):
        values = " ".join(_normalize(sheet.cell(row=row, column=col).value) for col in range(1, min(sheet.max_column, 32) + 1))
        if "ten vat tu" in values and ("don vi" in values or "khoi luong" in values):
            return row
    return None


def _is_kaiyo_quote_header(sheet, header_row: int) -> bool:
    values = " ".join(
        _normalize(sheet.cell(row=header_row, column=col).value)
        for col in range(1, min(sheet.max_column, 32) + 1)
    )
    return "ten vat tu" in values and "don vi" in values and ("don gia" in values or "thanh tien" in values)


def _find_kaiyo_data_start_row(sheet, header_row: int) -> Optional[int]:
    for row in range(header_row + 1, min(sheet.max_row, header_row + 40) + 1):
        description = _normalize(sheet.cell(row=row, column=2).value)
        qty = _numeric_value(sheet.cell(row=row, column=6).value)
        unit_price = _numeric_value(sheet.cell(row=row, column=7).value)
        item_no = _numeric_value(sheet.cell(row=row, column=1).value)
        if item_no is not None and row > header_row + 1:
            return row
        if description and item_no is not None and (qty is not None or unit_price is not None):
            return row
    return None


def _find_kaiyo_summary_start_row(sheet, data_start: int) -> Optional[int]:
    for row in range(data_start, sheet.max_row + 1):
        values = " ".join(_normalize(sheet.cell(row=row, column=col).value) for col in range(1, min(sheet.max_column, 10) + 1))
        if "tong cong" in values and ("truoc thue" in values or "gia truoc thue" in values):
            return row
        if row > data_start and "tong cong" in values and _numeric_value(sheet.cell(row=row, column=8).value) is not None:
            return row
    return None


def _find_kaiyo_notes_start_row(sheet, data_start: int) -> Optional[int]:
    for row in range(data_start, min(sheet.max_row, data_start + 120) + 1):
        values = " ".join(_normalize(sheet.cell(row=row, column=col).value) for col in range(1, min(sheet.max_column, 10) + 1))
        if "viet bang chu" in values or "ghi chu" in values:
            return row
    return None


def _snapshot_row_style(sheet, source_row: int) -> Dict[int, Dict[str, Any]]:
    snapshot: Dict[int, Dict[str, Any]] = {}
    for col in range(1, sheet.max_column + 1):
        source = sheet.cell(source_row, col)
        snapshot[col] = {
            "style": copy(source._style) if source.has_style else None,
            "number_format": source.number_format,
            "alignment": copy(source.alignment) if source.alignment else None,
            "font": copy(source.font) if source.font else None,
            "fill": copy(source.fill) if source.fill else None,
            "border": copy(source.border) if source.border else None,
        }
    return snapshot


def _apply_row_style_snapshot(sheet, snapshot: Dict[int, Dict[str, Any]], target_row: int) -> None:
    for col, style in snapshot.items():
        target = sheet.cell(target_row, col)
        if style.get("style") is not None:
            target._style = copy(style["style"])
        if style.get("number_format"):
            target.number_format = style["number_format"]
        if style.get("alignment"):
            target.alignment = copy(style["alignment"])
        if style.get("font"):
            target.font = copy(style["font"])
        if style.get("fill"):
            target.fill = copy(style["fill"])
        if style.get("border"):
            target.border = copy(style["border"])


def _numeric_value(value: Any) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _copy_row_style(sheet, source_row: int, target_row: int) -> None:
    for col in range(1, sheet.max_column + 1):
        source = sheet.cell(source_row, col)
        target = sheet.cell(target_row, col)
        if source.has_style:
            target._style = copy(source._style)
        if source.number_format:
            target.number_format = source.number_format
        if source.alignment:
            target.alignment = copy(source.alignment)
        if source.font:
            target.font = copy(source.font)
        if source.fill:
            target.fill = copy(source.fill)
        if source.border:
            target.border = copy(source.border)


def _ensure_rows(sheet, data_start: int, item_count: int, template_row: int) -> None:
    available = max(0, sheet.max_row - data_start + 1)
    needed = item_count + 3
    if available < needed:
        sheet.insert_rows(data_start + available, needed - available)
    for row in range(data_start, data_start + needed):
        _copy_row_style(sheet, template_row, row)


def _set_first_matching_cell(sheet, labels: List[str], min_row: int, max_row: int, value: Any, offset_cols: int = 1) -> None:
    normalized_labels = [_normalize(label) for label in labels]
    for row in range(min_row, max_row + 1):
        for col in range(1, min(sheet.max_column, 10) + 1):
            cell_value = _normalize(sheet.cell(row=row, column=col).value)
            if any(label in cell_value for label in normalized_labels):
                sheet.cell(row=row, column=col + offset_cols, value=value)
                return


def _secondary_width(item: Dict[str, Any]) -> Any:
    return item.get("diameter") if item.get("category") in ["TRANSITION", "REDUCER"] else item.get("length")


def _secondary_height(item: Dict[str, Any]) -> Any:
    return item.get("diameter") if item.get("category") in ["TRANSITION", "REDUCER"] else item.get("thickness")


def _area_per_item(item: Dict[str, Any], qty: float) -> Any:
    area = item.get("calculated_area")
    if area is None:
        return None
    return round(float(area) / qty, 4) if qty else area


def _infer_template_quote_code(item: Dict[str, Any]) -> str:
    category = item.get("category")
    description = _normalize(item.get("description", ""))
    if category in ["SUPPLY_DUCT", "RETURN_DUCT", "FRESH_AIR_DUCT", "EXHAUST_AIR_DUCT", "SMOKE_DUCT"]:
        return "t"
    if category == "END_CAP":
        return "tb"
    if category == "REDUCER":
        return "g"
    if category == "TRANSITION":
        return "vt" if "ø" in description or "phi" in description else "g"
    if category == "ELBOW":
        return "cv"
    if category == "TEE":
        return "tt"
    if "zet" in description or "down" in description:
        return "d"
    return ""


def _replace_sheet(workbook: Workbook, title: str):
    if title in workbook.sheetnames:
        old_sheet = workbook[title]
        idx = workbook.sheetnames.index(title)
        workbook.remove(old_sheet)
        return workbook.create_sheet(title=title, index=idx)
    return workbook.create_sheet(title=title)


def _remove_sheet_if_exists(workbook: Workbook, title: str) -> None:
    if title in workbook.sheetnames:
        workbook.remove(workbook[title])


def _fill_placeholders(workbook: Workbook, summary: Dict[str, float]) -> None:
    replacements = {
        "{TOTAL_AREA}": summary.get("total_area", 0.0),
        "{SUBTOTAL}": _export_money(summary.get("subtotal", 0.0)),
        "{PROFIT}": _export_money(summary.get("profit", 0.0)),
        "{VAT}": _export_money(summary.get("vat", 0.0)),
        "{GRAND_TOTAL}": _export_money(summary.get("grand_total", 0.0)),
    }
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value in replacements:
                    cell.value = replacements[cell.value]


def _write_items_sheet(sheet, items: List[Dict[str, Any]], summary: Dict[str, float]) -> None:
    export_columns = _export_columns()
    summary_columns = _summary_columns()
    title_fill = PatternFill("solid", fgColor="1F4E78")
    header_fill = PatternFill("solid", fgColor="D9EAF7")
    title_font = Font(color="FFFFFF", bold=True, size=14)
    header_font = Font(bold=True)

    sheet["A1"] = "Báo giá HVAC AI"
    sheet["A1"].font = title_font
    sheet["A1"].fill = title_fill
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(export_columns))

    sheet["A3"] = "Tổng hợp"
    sheet["A3"].font = Font(bold=True)
    for idx, (key, label) in enumerate(summary_columns, start=4):
        sheet.cell(idx, 1, label)
        sheet.cell(idx, 2, len(items) if key == "item_count" else summary.get(key, 0.0))

    header_row = len(summary_columns) + 6
    for col_idx, (_, header) in enumerate(export_columns, start=1):
        cell = sheet.cell(header_row, col_idx, header)
        cell.font = header_font
        cell.fill = header_fill

    for row_idx, item in enumerate(items, start=header_row + 1):
        for col_idx, (key, _) in enumerate(export_columns, start=1):
            value = item.get(key)
            if key == "warnings" and isinstance(value, list):
                value = "; ".join(value)
            sheet.cell(row_idx, col_idx, value)

    _autosize_columns(sheet, max_width=42)
    sheet.freeze_panes = sheet.cell(header_row + 1, 1)
    sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(export_columns))}{max(header_row, header_row + len(items))}"


def _autosize_columns(sheet, max_width: int = 40) -> None:
    for column_cells in sheet.columns:
        letter = get_column_letter(column_cells[0].column)
        length = 0
        for cell in column_cells:
            value = cell.value
            if value is None:
                continue
            length = max(length, len(str(value)))
        sheet.column_dimensions[letter].width = min(max(length + 2, 10), max_width)
