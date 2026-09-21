"""Parse input BOQ files into a normalised list of product items.

Inputs come in two broad shapes:

1. *Simple* — a single sheet with STT / Tên / Đơn vị / Khối lượng columns
   (e.g. ``data/BOQBN.xls``). May include section-title rows
   ("NHÀ XƯỞNG 1", ...) that have no numeric STT.
2. *Detailed* — a "BG " sheet holding the quote header plus a name list, and a
   second sheet holding the area table (Mã SP / W1 / H1 / L ...) with
   quantities (e.g. ``data/BTN1205926.xlsx``).

The parser:
  * detects item blocks on every sheet,
  * merges items across sheets by normalised name,
  * extracts quote header fields (Kính Gửi, Số BG, Ngày BG, ...).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .excel_reader import read_excel

_COL_NAME = ("tên công tác", "tên vật tư", "sản phẩm", "tên")
_COL_UNIT = ("đơn vị", "đvt")
_COL_QTY = ("khối lượng", "số lượng")
_COL_NOTE = ("ghi chú",)
_COL_MASP = ("mã sp",)
_COL_MATERIAL = ("vật liệu", "quy cách")
_COL_ORIGIN = ("xuất xứ", "nhãn hiệu")

_DIM_COLS = {
    "w1": "w1", "h1": "h1", "w2": "w2", "h2": "h2",
    "w3": "w3", "h3": "h3", "l/h": "l", "r/d": "r", "e": "e",
    "diện tích /cái ": "area",
}

# rows that terminate an item block
_STOP_ROWS = ("tổng cộng", "tổng thanh toán", "viết bằng chữ", "ghi chú")

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


def _cell(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _norm_name(s: str) -> str:
    return " ".join(s.split()).lower()


def _is_int(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return float(v).is_integer()
    if isinstance(v, str):
        t = v.strip()
        return t.isdigit()
    return False


def _as_num(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        t = v.strip().replace(",", ".")
        try:
            return float(t)
        except ValueError:
            return v
    return v


def _find_header_row(grid: list[list[Any]]) -> int | None:
    """Row index (0-based) of the STT header that also has a name column."""
    for i, row in enumerate(grid):
        cells = [_cell(c).lower() for c in row]
        if "stt" not in cells:
            continue
        joined = " | ".join(cells)
        if any(kw in joined for kw in _COL_NAME):
            return i
    return None


def _col_index_in_rows(grid: list[list[Any]], rows: list[int],
                       keywords: tuple[str, ...]) -> int | None:
    for i in rows:
        if i < 0 or i >= len(grid):
            continue
        for j, v in enumerate(grid[i]):
            s = _cell(v).lower().strip()
            for kw in keywords:
                if kw in s:
                    return j
    return None


def _parse_header(grid: list[list[Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in grid:
        n = len(row)
        for j, v in enumerate(row):
            s = _cell(v).lower()
            if not s:
                continue
            # normalise label: strip trailing ':' and collapse spaces
            lbl_norm = " ".join(s.rstrip(":").split())
            key = None
            for lbl, canon in _HEADER_LABELS.items():
                if lbl_norm == lbl:
                    key = canon
                    break
            if key is None:
                continue
            val = None
            for k in range(j + 1, min(j + 4, n)):
                c = row[k]
                t = _cell(c)
                if not t:
                    continue
                if t.startswith(":"):
                    val = t[1:].strip()
                else:
                    val = val or t
                break
            if val and key not in out:
                out[key] = val
    return out


def _extract_items(grid: list[list[Any]], hdr: int) -> list[dict]:
    window = [hdr - 1, hdr, hdr + 1]
    c_stt = _col_index_in_rows(grid, [hdr], ("stt",))
    if c_stt is None:
        c_stt = 0
    c_name = _col_index_in_rows(grid, window, _COL_NAME)
    c_unit = _col_index_in_rows(grid, window, _COL_UNIT)
    c_qty = _col_index_in_rows(grid, window, _COL_QTY)
    c_note = _col_index_in_rows(grid, window, _COL_NOTE)
    c_masp = _col_index_in_rows(grid, window, _COL_MASP)
    c_mat = _col_index_in_rows(grid, window, _COL_MATERIAL)
    c_org = _col_index_in_rows(grid, window, _COL_ORIGIN)

    dim_map: dict[str, int] = {}
    for i in window:
        if i < 0 or i >= len(grid):
            continue
        for j, v in enumerate(grid[i]):
            s = _cell(v).lower().strip()
            if s in _DIM_COLS and _DIM_COLS[s] not in dim_map:
                dim_map[_DIM_COLS[s]] = j

    def g(row: list[Any], idx: int | None) -> Any:
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    items: list[dict] = []
    for row in grid[hdr + 1:]:
        stt_v = row[c_stt] if c_stt < len(row) else None
        name = _cell(g(row, c_name))

        if name and _norm_name(name) in _STOP_ROWS:
            break

        # section title: text in the STT column but empty name column
        if not name:
            stt_txt = _cell(stt_v)
            if stt_txt and not _is_int(stt_v):
                items.append({"is_section": True, "ten": stt_txt})
            continue

        if not _is_int(stt_v):
            items.append({"is_section": True, "ten": name})
            continue

        item: dict[str, Any] = {
            "is_section": False,
            "stt": int(float(stt_v)),
            "ten": name,
            "don_vi": _cell(g(row, c_unit)),
            "khoi_luong": _as_num(g(row, c_qty)),
            "ghi_chu": _cell(g(row, c_note)),
            "ma_sp": _cell(g(row, c_masp)),
            "vat_lieu": _cell(g(row, c_mat)),
            "xuat_xu": _cell(g(row, c_org)),
        }
        for canon, j in dim_map.items():
            item[canon] = _as_num(g(row, j))
        items.append(item)

    return items


def _merge_by_stt(primary: list[dict], others: list[list[dict]]) -> list[dict]:
    """Merge other sheets into the primary items list by STT.

    Fills missing fields on primary items from matching STT rows in other
    sheets. Sections and duplicate names are preserved (STT, not name, is the
    merge key — the same product can legitimately appear in several sections).
    """
    result = [dict(it) for it in primary]
    primary_by_stt: dict[int, dict] = {}
    for it in result:
        if not it.get("is_section") and it.get("stt") is not None:
            primary_by_stt.setdefault(int(it["stt"]), it)

    for other in others:
        for it in other:
            if it.get("is_section") or it.get("stt") is None:
                continue
            tgt = primary_by_stt.get(int(it["stt"]))
            if tgt is None:
                continue
            for k, v in it.items():
                if k in ("is_section", "stt"):
                    continue
                if v in (None, ""):
                    continue
                if tgt.get(k) in (None, ""):
                    tgt[k] = v
    return result


def _qty_count(items: list[dict]) -> int:
    return sum(1 for it in items
               if not it.get("is_section") and it.get("khoi_luong") is not None)


def parse_input(path: str | Path) -> dict[str, Any]:
    """Return ``{file, header, items, source_sheet}`` for one input BOQ."""
    sheets = read_excel(path)

    header: dict[str, Any] = {}
    sheet_items: list[tuple[str, list[dict]]] = []

    for sh in sheets:
        grid = sh["grid"]
        h = _parse_header(grid)
        header.update({k: v for k, v in h.items() if v})
        hdr = _find_header_row(grid)
        if hdr is None:
            continue
        items = _extract_items(grid, hdr)
        if items:
            sheet_items.append((sh["name"], items))

    if not sheet_items:
        return {"file": str(path), "header": header, "items": [], "source_sheet": None}

    # primary sheet = the one with the most rows carrying a quantity
    sheet_items.sort(key=lambda si: (-_qty_count(si[1]), -len(si[1])))
    primary_name, primary = sheet_items[0]
    others = [items for _, items in sheet_items[1:]]

    items = _merge_by_stt(primary, others)
    return {
        "file": str(path),
        "header": header,
        "items": items,
        "source_sheet": ",".join(name for name, _ in sheet_items),
    }
