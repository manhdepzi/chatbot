"""Extract priced product rows (and the Mã SP legend) from golden quotations.

Each golden file is a finished quotation. We walk every sheet, find item blocks
(an STT + name header row), and emit one document per priced row. A row's
``text`` (the field used for retrieval) is the product name; everything else
(unit, quantity, unit price, material, origin, area dimensions, price factors)
is stored in ``metadata``.

For .xlsx files the unit-price and amount cells' formulas are also learned as
*recipes* in item fields (``meta.cong_thuc`` / ``meta.cong_thuc_tt``, e.g.
``ROUND([area]*[gia_ton]*[he_so],0)``): helper cells of the same row are
inlined, constants kept. A recipe is stored only if re-evaluating it with the
row's own values gives the value saved in the file (an equivalent result).
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any

from openpyxl.utils import get_column_letter

from ..loaders.excel_reader import read_excel
from ..pricing import formula as fx

_COL_NAME = ("tên công tác", "tên vật tư", "sản phẩm", "tên")
_COL_UNIT = ("đơn vị", "đvt")
_COL_QTY = ("khối lượng", "số lượng")
_COL_PRICE = ("đơn giá",)
_COL_AMOUNT = ("thành tiền",)
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
    "diện tích /cái": "area",
}

_SIZE_FIELDS = {"w1", "h1", "w2", "h2", "w3", "h3", "l", "r", "e"}

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


def _recipe(fws, grid: list[list[Any]], row: int, col: int,
            fields: dict[int, str], labels: dict[int, str] | None = None) -> dict | None:
    """Formula of cell (row, col) (1-based) as a recipe in item fields.

    Same-row cells of known field columns become ``[field]`` variables;
    same-row helper cells are inlined (their formula, or their constant);
    constant cells elsewhere (price tables) are inlined as numbers. Anything
    else (cross-sheet refs, ranges, other rows' formulas, text) makes the
    formula unusable. Returns ``{recipe, value, mo_ta, dinh_nghia}`` when
    re-evaluation with the row's values matches the cached value, else None.

    ``mo_ta`` / ``dinh_nghia`` are the readable form: helper cells stay named
    after their golden column header (``[m dài khung]``) and are defined
    separately (``m dài khung = ([w1]+[h1])*2/1000``, ``đơn giá khung = 100000``).
    """
    labels = labels or {}
    raw = fws.cell(row, col).value
    if not (isinstance(raw, str) and raw.startswith("=")):
        return None

    def cached(r: int, c: int) -> Any:
        if 0 < r <= len(grid) and 0 < c <= len(grid[r - 1]):
            return grid[r - 1][c - 1]
        return None

    def inline(node, depth: int):
        def fn(n):
            if n[0] == "range":
                raise fx.FormulaError("range")
            if n[0] != "ref":
                return None
            if n[1]:
                raise fx.FormulaError("other sheet")
            c, r = n[2], n[3]
            f = fws.cell(r, c).value
            if r == row and c in fields:
                # some sheets reuse size columns (W2, H3...) as helpers holding
                # their own arithmetic: inline those; sizes typed in or cut
                # from the name text stay variables, like prices and factors
                if fields[c] in _SIZE_FIELDS and isinstance(f, str) and f.startswith("=") \
                        and depth < 6:
                    try:
                        return ("grp", inline(fx.parse(f), depth + 1))
                    except fx.FormulaError:
                        pass
                return ("var", fields[c])
            if isinstance(f, str) and f.startswith("="):
                if r != row or depth >= 6:
                    raise fx.FormulaError("formula outside the row")
                return ("grp", inline(fx.parse(f), depth + 1))
            v = cached(r, c)
            if v is None or v == "":
                return ("num", 0.0)
            if isinstance(v, bool) or _num(v) is None or isinstance(_num(v), str):
                raise fx.FormulaError("text constant")
            return ("num", float(_num(v)))
        return fx.transform(node, fn)

    defs: dict[str, str] = {}

    def name_of(c: int) -> str:
        label = labels.get(c, "")
        if not label or label in _SIZE_FIELDS or label in fields.values():
            return f"cột {get_column_letter(c)}"
        return label

    def explain(node, depth: int):
        # same structure as ``inline`` but same-row helpers stay named
        def fn(n):
            if n[0] != "ref":
                return None
            c, r = n[2], n[3]
            f = fws.cell(r, c).value
            is_formula = isinstance(f, str) and f.startswith("=")
            if r == row and c in fields and not (
                    fields[c] in _SIZE_FIELDS and is_formula and depth < 6):
                return ("var", fields[c])
            if r != row:
                return ("num", float(_num(cached(r, c)) or 0))
            label = name_of(c)
            if label not in defs:
                defs[label] = ""  # guards against cycles
                if is_formula:
                    defs[label] = fx.render(explain(fx.parse(f), depth + 1))
                else:
                    defs[label] = fx.render(("num", float(_num(cached(r, c)) or 0)))
            return ("var", label)
        return fx.transform(node, fn)

    try:
        node = fx.canonical(inline(fx.parse(raw), 0))
        by_field = {f: c for c, f in fields.items()}
        value = fx.evaluate(node, lambda n: cached(row, by_field[n[1]]))
        readable = fx.render(fx.canonical(explain(fx.parse(raw), 0)))
    except (fx.FormulaError, ZeroDivisionError, ValueError, OverflowError, TypeError):
        return None
    saved = cached(row, col)
    if saved is not None and not fx.close(value, _num(saved)):
        return None
    return {"recipe": fx.render(node), "value": value,
            "mo_ta": readable, "dinh_nghia": defs}


def _extract_rows(grid: list[list[Any]], hdr: int, fws=None) -> list[dict]:
    window = [hdr - 1, hdr, hdr + 1]
    c_stt = _col_in_rows(grid, [hdr], ("stt",)) or 0
    c_name = _col_in_rows(grid, window, _COL_NAME)
    c_unit = _col_in_rows(grid, window, _COL_UNIT)
    c_qty = _col_in_rows(grid, window, _COL_QTY)
    c_price = _col_in_rows(grid, window, _COL_PRICE)
    c_amount = _col_in_rows(grid, window, _COL_AMOUNT)
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

    # 1-based column -> header text, to name helper cells in formulas
    labels: dict[int, str] = {}
    for i in (hdr, hdr - 1, hdr + 1):
        if 0 <= i < len(grid):
            for j, v in enumerate(grid[i]):
                t = " ".join(_cell(v).split()).lower()
                if t and (j + 1) not in labels:
                    labels[j + 1] = t

    # 1-based column -> item field, for formula recipes
    fields = {j + 1: f for f, j in dim_map.items()}
    for f, j in (("don_gia", c_price), ("khoi_luong", c_qty), ("ma_sp", c_masp),
                 ("gia_ton", c_gia_ton), ("he_so", c_he_so)):
        if j is not None:
            fields.setdefault(j + 1, f)

    out: list[dict] = []
    for i, row in enumerate(grid[hdr + 1:], start=hdr + 2):
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
            # None: the sheet has no Mã SP column (unknown, not "no code")
            "ma_sp": _cell(g(row, c_masp)) if c_masp is not None else None,
            "vat_lieu": _cell(g(row, c_mat)),
            "xuat_xu": _cell(g(row, c_org)),
            "ghi_chu": _cell(g(row, c_note)),
            "gia_ton": _num(g(row, c_gia_ton)),
            "he_so": _num(g(row, c_he_so)),
            "ty_ren": _num(g(row, c_ty_ren)),
        }
        for canon, j in dim_map.items():
            meta[canon] = _num(g(row, j))
        if fws is not None:
            for key, j in (("cong_thuc", c_price), ("cong_thuc_tt", c_amount)):
                rec = _recipe(fws, grid, i, j + 1, fields, labels) if j is not None else None
                if rec is None:
                    continue
                meta[key] = rec["recipe"]
                if key == "cong_thuc":
                    meta["cong_thuc_mo_ta"] = rec["mo_ta"]
                    meta["cong_thuc_dinh_nghia"] = rec["dinh_nghia"]
                    # file saved without cached values: the formula is the value
                    if meta["don_gia"] is None:
                        meta["don_gia"] = rec["value"]
        out.append({"text": name, "meta": meta})
    return out


def _extract_legend(grid: list[list[Any]], hdr: int | None) -> list[tuple[str, str]]:
    """The "Mã SP" legend above the item table: (product type, code) pairs.

    The legend is a two-column block whose code column is headed "Mã SP"
    (e.g. "Ống" -> t, "Cút / Co" -> cv); it sits above the item header row.
    """
    stop = hdr if hdr is not None else min(len(grid), 40)
    for i in range(stop):
        for j, v in enumerate(grid[i]):
            if j == 0 or _norm(_cell(v)) != "mã sp":
                continue
            pairs: list[tuple[str, str]] = []
            for k in range(i + 1, stop):
                label, code = _cell(grid[k][j - 1]), _cell(grid[k][j])
                if not label and not code:
                    break
                if label and code:
                    pairs.append((label, code))
            if pairs:
                return pairs
    return []


def parse_legend_docs(path: str | Path) -> list[dict]:
    """Mã SP legend of every sheet of one workbook as knowledge documents."""
    path = Path(path)
    docs: list[dict] = []
    for sh in read_excel(path):
        for label, code in _extract_legend(sh["grid"], _find_header_row(sh["grid"])):
            docs.append({
                "text": label,
                "meta": {"kind": "ma_sp", "ma_sp": code},
                "source": f"{path.name}:{sh['name']}",
                "priced": False,
            })
    return docs


def _formula_sheets(path: Path) -> dict[str, Any]:
    """Sheet name -> openpyxl worksheet holding formulas (.xlsx only)."""
    if path.suffix.lower() != ".xlsx":
        return {}
    import warnings

    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path)
    return {ws.title: ws for ws in wb.worksheets}


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
            formulas = _formula_sheets(path)
        except Exception as e:  # noqa: BLE001
            print(f"[golden] skip {path.name}: {e}")
            continue
        for sh in sheets:
            hdr = _find_header_row(sh["grid"])
            for label, code in _extract_legend(sh["grid"], hdr):
                docs.append({
                    "text": label,
                    "meta": {"kind": "ma_sp", "ma_sp": code},
                    "source": f"{path.name}:{sh['name']}",
                    "priced": False,
                })
            if hdr is None:
                continue
            for doc in _extract_rows(sh["grid"], hdr, formulas.get(sh["name"])):
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
