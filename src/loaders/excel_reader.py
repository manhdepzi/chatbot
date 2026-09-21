"""Read .xls / .xlsx files into raw 2-D cell grids.

The readers return ``list[sheet]`` where each sheet is
``{"name": str, "grid": list[list[value]]}``. Rows are padded to a common
width so the parser can index columns consistently.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any


def _norm(v: Any) -> Any:
    """Normalise cell values: strip strings, drop empty-ish values to None."""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s if s != "" else None
    if isinstance(v, float) and v != v:  # NaN
        return None
    return v


def _pad(grid: list[list[Any]]) -> list[list[Any]]:
    if not grid:
        return grid
    width = max((len(r) for r in grid), default=0)
    for r in grid:
        r.extend([None] * (width - len(r)))
    return grid


def read_xlsx(path: Path) -> list[dict]:
    import openpyxl

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, data_only=True)

    sheets = []
    for ws in wb.worksheets:
        grid = []
        for row in ws.iter_rows(values_only=True):
            grid.append([_norm(c) for c in row])
        # trim fully-empty trailing rows/cols
        sheets.append({"name": ws.title, "grid": _pad(grid)})
    return sheets


def read_xls(path: Path) -> list[dict]:
    import xlrd

    wb = xlrd.open_workbook(str(path), formatting_info=False)
    sheets = []
    for sh in wb.sheets():
        grid = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                row.append(_norm(sh.cell_value(r, c)))
            grid.append(row)
        sheets.append({"name": sh.name, "grid": _pad(grid)})
    return sheets


def read_excel(path: str | Path) -> list[dict]:
    """Read an Excel file, dispatching on extension."""
    p = Path(path)
    if p.suffix.lower() == ".xls":
        return read_xls(p)
    return read_xlsx(p)
