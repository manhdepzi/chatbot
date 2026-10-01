"""Build an output quotation by filling the Kaiyo template.

The builder copies ``report-template/kaiyo_quotation_template.xlsx`` and fills
the ``Mẫu - xây lắp`` sheet, preserving the template's styles, merges and
number formats. Column positions are detected from the template's own header
row so the same code works across Kaiyo template variants.
"""
from __future__ import annotations

import copy
import logging
import zipfile
from pathlib import Path
from typing import Any

import openpyxl

from .. import config
from ..pricing import formula as fx

log = logging.getLogger("report")

# header labels (lowercased) -> canonical field. Dimension tokens are matched
# exactly (or as a prefix) to avoid single-char false matches.
_DIM_LABELS: list[tuple[tuple[str, ...], str]] = [
    (("mã sp",), "ma_sp"),
    (("w1",), "w1"),
    (("h1",), "h1"),
    (("w2",), "w2"),
    (("h2",), "h2"),
    (("w3",), "w3"),
    (("h3",), "h3"),
    (("l/h",), "l"),
    (("r/d",), "r"),
    (("e",), "e"),
    (("diện tích",), "area"),
    (("giá tôn",), "gia_ton"),
    (("hệ số",), "he_so"),
]

_CORE_LABELS: list[tuple[tuple[str, ...], str]] = [
    (("stt",), "stt"),
    (("tên vật tư", "tên công tác", "sản phẩm", "tên"), "ten"),
    (("vật liệu chế tạo", "vật liệu", "quy cách"), "vat_lieu"),
    (("xuất xứ", "nhãn hiệu"), "xuat_xu"),
    (("đơn vị", "đvt"), "don_vi"),
    (("khối lượng", "số lượng"), "khoi_luong"),
    (("đơn giá",), "don_gia"),
    (("thành tiền",), "thanh_tien"),
    (("ghi chú",), "ghi_chu"),
]

_TOTAL_LABELS = {
    "tổng cộng giá trước thuế": "truoc_thue",
    "thuế gtgt 10%": "vat10",
    "thuế gtgt 8%": "vat8",
    "tổng cộng giá sau thuế": "sau_thue",
}


def _cell(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


def _find_sheet(wb, name_hint: str = "Mẫu - xây lắp"):
    if name_hint in wb.sheetnames:
        return wb[name_hint]
    # fallback: pick the largest sheet (most rows)
    return max(wb.worksheets, key=lambda ws: ws.max_row)


def _find_header_row(ws) -> int | None:
    for r in range(1, min(ws.max_row, 40) + 1):
        cells = [_norm(_cell(ws.cell(r, c).value)) for c in range(1, ws.max_column + 1)]
        if "stt" in cells and any("tên" in x or "sản phẩm" in x for x in cells):
            return r
    return None


def _map_columns(ws, header_row: int) -> dict[str, int]:
    """Map canonical field -> 1-based column index from the header row.

    Dimension tokens (w1, h1, e, ...) match exactly to avoid single-char false
    positives; core fields match as substrings, with ``ten`` bound to the first
    name-like column and ``stt`` to the column literally labelled "stt".
    """
    mapping: dict[str, int] = {}
    taken: set[int] = set()

    # first pass: core columns, left-to-right
    for c in range(1, ws.max_column + 1):
        label = _norm(_cell(ws.cell(header_row, c).value))
        if not label:
            continue
        for keywords, canon in _CORE_LABELS:
            if canon in mapping:
                continue
            if canon == "stt":
                if label != "stt":
                    continue
            else:
                if not any(kw in label for kw in keywords):
                    continue
            mapping[canon] = c
            taken.add(c)
            break

    # second pass: dimension columns (exact match)
    for c in range(1, ws.max_column + 1):
        if c in taken:
            continue
        label = _norm(_cell(ws.cell(header_row, c).value))
        if not label:
            continue
        for keywords, canon in _DIM_LABELS:
            if canon in mapping:
                continue
            if canon == "area":
                # "DIỆN TÍCH /CÁI " normalises to "diện tích /cái"; match prefix
                if label.startswith("diện tích"):
                    mapping[canon] = c
                    taken.add(c)
                    break
            if label in keywords:
                mapping[canon] = c
                taken.add(c)
                break

    # the area table repeats the product name in a column immediately left of
    # Mã SP (its header label is merged K20:K21, so it is absent from the
    # header row itself). Bind it as ``ten_dim`` = the column just before Mã SP.
    if "ma_sp" in mapping and mapping["ma_sp"] > 1:
        mapping["ten_dim"] = mapping["ma_sp"] - 1

    return mapping


def _find_total_row(ws, start_row: int) -> int | None:
    for r in range(start_row, ws.max_row + 1):
        for c in range(1, ws.max_column + 1):
            label = _norm(_cell(ws.cell(r, c).value))
            if "tổng cộng giá trước thuế" in label:
                return r
    return None


def _copy_style(src, dst):
    if src.has_style:
        dst.font = copy.copy(src.font)
        dst.fill = copy.copy(src.fill)
        dst.border = copy.copy(src.border)
        dst.alignment = copy.copy(src.alignment)
        dst.number_format = src.number_format
        dst.protection = copy.copy(src.protection)


def _fill_masp_legend(ws, header_row: int, legend: list[tuple[str, str]]) -> None:
    """Write the 'Mã SP' legend: the product types and codes used in the area table.

    The template's own legend is sample content (it is indexed as knowledge,
    not copied): its rows are cleared and replaced by ``legend`` — the
    (type, code) pairs learned from the knowledge base whose code this report
    uses. The block spans two columns, labels left of the "Mã SP" header
    column, and may grow down to the row above the area-table title.
    """
    for r in range(1, header_row):
        for c in range(2, ws.max_column + 1):
            if _norm(_cell(ws.cell(r, c).value)) != "mã sp":
                continue
            style = (ws.cell(r + 1, c - 1), ws.cell(r + 1, c))
            last = header_row - 2  # header_row - 1 holds the area-table title
            for rr in range(r + 1, last + 1):
                ws.cell(rr, c - 1).value = None
                ws.cell(rr, c).value = None
            rows = legend[:max(last - r, 0)]
            for i, (label, code) in enumerate(rows):
                lc, cc = ws.cell(r + 1 + i, c - 1), ws.cell(r + 1 + i, c)
                _copy_style(style[0], lc)
                _copy_style(style[1], cc)
                lc.value, cc.value = label, code
            return


def _parse_or_none(text: Any):
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return fx.parse(text)
    except fx.FormulaError:
        return None


def _row_patterns(ws, row: int) -> dict[int, Any]:
    """Column -> parsed formula of the template's reference data row."""
    out = {}
    for c in range(1, ws.max_column + 1):
        v = ws.cell(row, c).value
        if isinstance(v, str) and v.startswith("="):
            node = _parse_or_none(v)
            if node is not None:
                out[c] = node
    return out


def _vars_to_row(node, col: dict[str, int], row: int):
    """A recipe in item fields (``[w1]``...) as cell refs of ``row``; None if
    a field has no column in the template."""
    if node is None:
        return None
    if any(v not in col for v in fx.variables(node)):
        return None
    return fx.transform(node, lambda n: ("ref", None, col[n[1]], row, False, False)
                        if n[0] == "var" else None)


class _Cells:
    """Writes cells, preferring a formula when it evaluates — against the
    values already written — to the same result as the computed value."""

    def __init__(self, ws):
        self.ws = ws
        self.vals: dict[tuple[int, int], Any] = {}

    def _lookup(self, n) -> Any:
        if n[0] != "ref" or n[1]:
            raise fx.FormulaError("unresolvable reference")
        key = (n[3], n[2])
        if key in self.vals:
            return self.vals[key]
        v = self.ws.cell(n[3], n[2]).value
        if isinstance(v, str) and v.startswith("="):
            raise fx.FormulaError("unevaluated formula cell")
        return v

    def put(self, r: int, c: int, value: Any, node=None) -> None:
        cell = self.ws.cell(r, c)
        if node is not None:
            try:
                got = fx.evaluate(node, self._lookup)
            except (fx.FormulaError, ZeroDivisionError, OverflowError, ValueError):
                got = _NO_RESULT
            if got is not _NO_RESULT and fx.close(got, value):
                cell.value = "=" + fx.render(node)
                self.vals[(r, c)] = got
                return
        cell.value = value
        self.vals[(r, c)] = value


_NO_RESULT = object()


def _check_images(wb) -> None:
    """openpyxl silently drops the template's pictures (the letterhead logo)
    when Pillow is not installed: say so instead of losing them quietly."""
    with zipfile.ZipFile(config.TEMPLATE_FILE) as z:
        in_file = sum(1 for n in z.namelist() if n.startswith("xl/media/"))
    loaded = sum(len(ws._images) for ws in wb.worksheets)
    if in_file and not loaded:
        log.warning("template images were not loaded (is Pillow installed? "
                    "pip install -r requirements.txt): the report will lack "
                    "the letterhead logo")


def build_report(items: list[dict[str, Any]], header: dict[str, Any],
                 out_path: str | Path, totals: dict[str, float],
                 legend: list[tuple[str, str]] | None = None,
                 amount_formula: str | None = None) -> str:
    """Fill the template and save to ``out_path``. Returns the output path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.load_workbook(config.TEMPLATE_FILE)
    ws = _find_sheet(wb)
    _check_images(wb)

    header_row = _find_header_row(ws)
    if header_row is None:
        raise RuntimeError("could not locate the item header row in template")

    _fill_masp_legend(ws, header_row, legend or [])

    col = _map_columns(ws, header_row)
    if "ten" not in col or "khoi_luong" not in col:
        raise RuntimeError("template header lacks name/quantity columns")

    first_data_row = header_row + 2  # template has a sub-header at +1
    total_row = _find_total_row(ws, first_data_row)

    # --- clear existing data rows between header and totals -----------------
    if total_row is None:
        total_row = first_data_row + len(items) + 6
    tpl_total_row = total_row
    n_rows_needed = len(items)
    n_available = (total_row - first_data_row) if total_row else 0

    # reference style row (a clean data row): use header_row+2
    ref_row = header_row + 2
    # the template's per-row formulas (area table name "=+B23", DIỆN TÍCH)
    patterns = _row_patterns(ws, ref_row)

    # clear old data cells
    for r in range(first_data_row, total_row):
        for c in range(1, ws.max_column + 1):
            ws.cell(r, c).value = None

    # insert rows if needed (before totals)
    extra = 0
    if n_rows_needed > n_available:
        extra = n_rows_needed - n_available
        ws.insert_rows(total_row, extra)
        total_row += extra

    cells = _Cells(ws)
    amount = _parse_or_none(amount_formula)

    # --- write items --------------------------------------------------------
    r = first_data_row
    for it in items:
        if it.get("is_section"):
            cells.put(r, col["stt"], it["ten"])
        else:
            cells.put(r, col["stt"], it.get("stt"))
            cells.put(r, col["ten"], it.get("ten"))
            for k in ("vat_lieu", "xuat_xu", "don_vi", "khoi_luong", "ghi_chu", "ma_sp",
                      "w1", "h1", "w2", "h2", "w3", "h3", "l", "r", "e",
                      "gia_ton", "he_so"):
                if k in col:
                    cells.put(r, col[k], it.get(k))
            # formula cells, written as formulas when they give the same result
            # (area table name, DIỆN TÍCH /CÁI, unit price, amount)
            at_row = lambda n: fx.shift_rows(n, lambda x: r if x == ref_row else x)  # noqa: E731
            for k in ("ten_dim", "area"):
                if k in col:
                    node = patterns.get(col[k])
                    cells.put(r, col[k], it.get("ten") if k == "ten_dim" else it.get(k),
                              at_row(node) if node is not None else None)
            if "don_gia" in col:
                cells.put(r, col["don_gia"], it.get("don_gia"),
                          _vars_to_row(_parse_or_none(it.get("cong_thuc")), col, r))
            if "thanh_tien" in col:
                cells.put(r, col["thanh_tien"], it.get("thanh_tien"),
                          _vars_to_row(amount, col, r))
        # copy style from reference row so new/kept rows look like the template
        for c in range(1, ws.max_column + 1):
            _copy_style(ws.cell(ref_row, c), ws.cell(r, c))
        r += 1

    # --- totals -------------------------------------------------------------
    # Each total line is a separate row: its label sits in a text column and
    # the value goes into the "thành tiền" (amount) column. The template's own
    # formulas (SUM of the item amounts, VAT...) are kept, re-anchored to the
    # rows inserted, whenever they reproduce the computed totals.
    amt_col = col.get("thanh_tien", 8)
    total_row_map: dict[str, int] = {}
    for r in range(first_data_row, ws.max_row + 1):
        for c in range(1, ws.max_column + 1):
            label = _norm(_cell(ws.cell(r, c).value))
            for lbl, canon in _TOTAL_LABELS.items():
                if lbl in label and canon not in total_row_map:
                    total_row_map[canon] = r
                    break
    last_tpl_data_row = tpl_total_row - 1
    for canon, r in sorted(total_row_map.items(), key=lambda kv: kv[1]):
        if canon not in totals:
            continue
        node = _parse_or_none(ws.cell(r, amt_col).value)
        if node is not None:
            node = fx.shift_rows(node, lambda x: x + extra if x >= last_tpl_data_row else x)
        cells.put(r, amt_col, totals[canon], node)

    # formulas are stored without cached results: let Excel compute on open
    wb.calculation.fullCalcOnLoad = True

    # --- header fields ------------------------------------------------------
    _fill_header(ws, header)

    wb.save(out_path)
    return str(out_path)


_HEADER_LABELS = {
    "kính gửi": "kinh_gui",
    "địa chỉ": "dia_chi",
    "mã số thuế": "ma_so_thue",
    "số đt": "so_dt",
    "người nhận": "nguoi_nhan",
    "số điện thoại": "sdt_nguoi_nhan",
    "dự án": "du_an",
    "số bg": "so_bg",
    "ngày bg": "ngay_bg",
    "hiệu lực": "hieu_luc",
    "tiến độ": "tien_do",
}


def _fill_header(ws, header: dict[str, Any]):
    """Write quote header values next to their labels.

    The value goes into the first cell right of the label that is outside the
    label's own merged range. Only the first occurrence of a label is filled:
    the buyer block comes before the seller block, which repeats "Số ĐT" and
    "Mã số thuế" with Kaiyo's own fixed values.
    """
    if not header:
        return
    anchor = {}
    for mr in ws.merged_cells.ranges:
        for rr in range(mr.min_row, mr.max_row + 1):
            for cc in range(mr.min_col, mr.max_col + 1):
                anchor[(rr, cc)] = (mr.min_row, mr.min_col)

    done: set[str] = set()
    for r in range(1, 30):
        for c in range(1, ws.max_column + 1):
            lbl = _norm(_cell(ws.cell(r, c).value)).rstrip(": ").strip()
            canon = _HEADER_LABELS.get(lbl)
            if canon is None or canon in done:
                continue
            done.add(canon)
            if not header.get(canon):
                continue
            own = anchor.get((r, c), (r, c))
            for cc in range(c + 1, ws.max_column + 1):
                tr, tc = anchor.get((r, cc), (r, cc))
                if (tr, tc) == own:
                    continue
                # the seller block prefixes its values with ": " in column C
                value = header[canon]
                if tc == 3:
                    value = f": {value}"
                ws.cell(tr, tc).value = value
                break
