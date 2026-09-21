"""Extract priced product rows from the golden quotation files.

Each golden file is a finished quotation. We walk every sheet, find item blocks
(an STT + name header row), and emit one document per priced row. A row's
``text`` (the field used for retrieval) is the product name; everything else
(unit, quantity, unit price, material, origin, area dimensions, price factors)
is stored in ``metadata``.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from ..loaders.excel_reader import read_excel

_COL_NAME = ("tên công tác", "tên vật tư", "sản phẩm", "tên")
_COL_UNIT = ("đơn vị", "đvt")
_COL_QTY = ("khối lượng", "số lượng")
_COL_PRICE = ("đơn giá",)
_COL_MASP = ("mã sp",)
_COL_MATERIAL = ("vật liệu", "quy cách", "kích thước")
_COL_ORIGIN = ("xuất xứ", "nhãn hiệu")
_COL_NOTE = ("ghi chú",)
_COL_GIA_TON = ("giá tôn",)
_COL_HE_SO = ("hệ số",)
_COL_TY_REN = ("ty ren", "mét dài ty")

_DIM_COLS = {
    "w1": "w1", "h1": "h1", "w2": "w2", "h2": "h2",
    "w3": "w3", "h3": "h3", "l/h": "l", "r/d": "r", "e": "e",
    "diện tích /cái ": "area",
}

_STOP_ROWS = ("tổng cộng", "tổng thanh toán", "viết bằng chữ", "ghi chú")


def _cell(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


def _is_int(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return float(v).is_integer()
    if isinstance(v, str):
        return v.strip().isdigit()
    return False


def _num(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        t = v.strip().replace(",", "")
        try:
            return float(t)
        except ValueError:
            return None
    if isinstance(v, _dt.datetime):
        return v.strftime("%d/%m/%Y")
    return v


def _find_header_row(grid: list[list[Any]]) -> int | None:
    for i, row in enumerate(grid):
        cells = [_cell(c).lower() for c in row]
        if "stt" not in cells:
            continue
        joined = " | ".join(cells)
        if any(kw in joined for kw in _COL_NAME):
            return i
    return None


def _col_in_rows(grid: list[list[Any]], rows: list[int],
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


def _extract_rows(grid: list[list[Any]], hdr: int) -> list[dict]:
    window = [hdr - 1, hdr, hdr + 1]
    c_stt = _col_in_rows(grid, [hdr], ("stt",)) or 0
    c_name = _col_in_rows(grid, window, _COL_NAME)
    c_unit = _col_in_rows(grid, window, _COL_UNIT)
    c_qty = _col_in_rows(grid, window, _COL_QTY)
    c_price = _col_in_rows(grid, window, _COL_PRICE)
    c_masp = _col_in_rows(grid, window, _COL_MASP)
    c_mat = _col_in_rows(grid, window, _COL_MATERIAL)
    c_org = _col_in_rows(grid, window, _COL_ORIGIN)
    c_note = _col_in_rows(grid, window, _COL_NOTE)
    c_gia_ton = _col_in_rows(grid, window, _COL_GIA_TON)
    c_he_so = _col_in_rows(grid, window, _COL_HE_SO)
    c_ty_ren = _col_in_rows(grid, window, _COL_TY_REN)

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

    out: list[dict] = []
    for row in grid[hdr + 1:]:
        stt_v = row[c_stt] if c_stt < len(row) else None
        name = _cell(g(row, c_name))
        if name and _norm(name) in _STOP_ROWS:
            break
        if not name or not _is_int(stt_v):
            continue

        meta: dict[str, Any] = {
            "stt": int(float(stt_v)),
            "don_vi": _cell(g(row, c_unit)),
            "khoi_luong": _num(g(row, c_qty)),
            "don_gia": _num(g(row, c_price)),
            "ma_sp": _cell(g(row, c_masp)),
            "vat_lieu": _cell(g(row, c_mat)),
            "xuat_xu": _cell(g(row, c_org)),
            "ghi_chu": _cell(g(row, c_note)),
            "gia_ton": _num(g(row, c_gia_ton)),
            "he_so": _num(g(row, c_he_so)),
            "ty_ren": _num(g(row, c_ty_ren)),
        }
        for canon, j in dim_map.items():
            meta[canon] = _num(g(row, j))
        out.append({"text": name, "meta": meta})
    return out


def parse_golden_dir(golden_dir: str | Path) -> list[dict]:
    """Return a flat list of documents ``{text, meta, source}`` from all
    golden files. Documents with neither a unit price nor a price factor are
    still kept (they teach classification), but flagged ``priced=False``.
    """
    docs: list[dict] = []
    gdir = Path(golden_dir)
    for path in sorted(gdir.iterdir()):
        if path.suffix.lower() not in (".xls", ".xlsx"):
            continue
        if path.name.startswith(("~$", ".")):
            continue
        try:
            sheets = read_excel(path)
        except Exception as e:  # noqa: BLE001
            print(f"[golden] skip {path.name}: {e}")
            continue
        for sh in sheets:
            hdr = _find_header_row(sh["grid"])
            if hdr is None:
                continue
            for doc in _extract_rows(sh["grid"], hdr):
                meta = doc["meta"]
                priced = meta.get("don_gia") is not None or (
                    meta.get("gia_ton") is not None)
                docs.append({
                    "text": doc["text"],
                    "meta": meta,
                    "source": f"{path.name}:{sh['name']}",
                    "priced": priced,
                })
    return docs
