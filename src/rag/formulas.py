"""Formula knowledge: the golden workbooks' price formulas as RAG documents.

``golden_parser`` turns each golden row's unit-price cell into a verified
recipe in item fields (``meta.cong_thuc``). Here the recipes are grouped into
one document per pricing method — the same formula with the same price
parameters (giá tôn / hệ số, which some sheets use as frame/blade prices) —
so the method itself can be retrieved from the vector DB and handed to the
LLM, which then picks a formula instead of guessing a price.

A formula document carries:
  * ``cong_thuc``   executable recipe, e.g. ``ROUND([area]*[gia_ton]*[he_so],0)``
  * ``mo_ta``       readable form named after the golden columns
  * ``dinh_nghia``  helper definitions (``m dài khung = ([w1]+[h1])*2/1000``)
  * ``kich_thuoc``  sizes an item must have to use it
  * ``gia_ton`` / ``he_so``  parameters the formula is used with
  * ``ma_sp`` / ``mac_dinh``  for ``[area]`` formulas: the shape code every row
    was measured with and the sizes that never vary across the rows (e.g. a
    damper body L=250) — defaults for an item that lacks them
  * ``vi_du``       example products: sizes -> unit price, source workbook
Each golden product row gets ``meta.cong_thuc_id`` pointing to its method.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from typing import Any

from ..pricing import formula as fx

KIND = "cong_thuc"
_SIZE_VARS = ("w1", "h1", "w2", "h2", "w3", "h3", "l", "r", "e", "area")
_PARAMS = ("gia_ton", "he_so")
_MAX_EXAMPLES = 5
# sizes that may default from the formula's rows (never the main W x H)
DEFAULTABLE = ("w2", "h2", "w3", "h3", "l", "r", "e")
_MAX_NAMES = 12


def _first_line(text: str) -> str:
    lines = [x.strip() for x in str(text).split("\n") if x.strip()]
    return lines[0] if lines else ""


def _param_key(meta: dict, used: set[str]) -> tuple:
    return tuple(round(float(meta.get(p) or 0), 6) if p in used else None for p in _PARAMS)


def formula_id(recipe: str, params: tuple) -> str:
    h = hashlib.sha1(f"{recipe}\x00{params}".encode("utf-8")).hexdigest()[:12]
    return f"F-{h}"


def build_formula_docs(docs: list[dict]) -> list[dict]:
    """Group golden rows by (recipe, parameters) into formula documents and
    link every row to its group via ``meta.cong_thuc_id``."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        m = d.get("meta", {})
        recipe = m.get("cong_thuc")
        if not recipe or m.get("kind"):
            continue
        try:
            used = fx.variables(fx.parse(recipe))
        except fx.FormulaError:
            continue
        fid = formula_id(recipe, _param_key(m, used))
        m["cong_thuc_id"] = fid
        groups[fid].append(d)

    out: list[dict] = []
    for fid, rows in groups.items():
        m0 = rows[0]["meta"]
        used = fx.variables(fx.parse(m0["cong_thuc"]))
        mo_ta = Counter(r["meta"].get("cong_thuc_mo_ta") or m0["cong_thuc"] for r in rows)
        best = mo_ta.most_common(1)[0][0]
        dinh_nghia = next((r["meta"].get("cong_thuc_dinh_nghia") for r in rows
                           if r["meta"].get("cong_thuc_mo_ta") == best), {}) or {}
        units = Counter(str(r["meta"].get("don_vi") or "").split("\n")[0].strip()
                        for r in rows if r["meta"].get("don_vi"))
        codes = Counter(str(r["meta"]["ma_sp"]).strip().lower() for r in rows
                        if r["meta"].get("ma_sp") is not None)
        names = list(dict.fromkeys(_first_line(r["text"]) for r in rows))

        examples = []
        for r in rows[:_MAX_EXAMPLES]:
            rm = r["meta"]
            ex = {"ten": _first_line(r["text"]), "don_gia": rm.get("don_gia"),
                  "nguon": r.get("source", "").split(":")[0]}
            ex.update({k: rm[k] for k in _SIZE_VARS if k in used and rm.get(k) is not None})
            examples.append(ex)

        meta: dict[str, Any] = {
            "kind": KIND,
            "id": fid,
            "cong_thuc": m0["cong_thuc"],
            "mo_ta": best,
            "dinh_nghia": dinh_nghia,
            "kich_thuoc": [k for k in _SIZE_VARS if k in used],
            "so_lan_dung": len(rows),
            "vi_du": examples,
            "nguon": sorted({r.get("source", "").split(":")[0] for r in rows}),
        }
        for p in _PARAMS:
            if p in used:
                meta[p] = m0.get(p)
        if units:
            meta["don_vi"] = units.most_common(1)[0][0]
        # the area the rows were priced with depends on their shape code and
        # sizes: carry the code all rows share and the sizes none of them vary
        if len(codes) == 1 and next(iter(codes)):
            meta["ma_sp"] = next(iter(codes))
        if "area" in used and len(rows) > 1:
            defaults = {}
            for k in DEFAULTABLE:
                vals = {r["meta"].get(k) for r in rows}
                if len(vals) == 1 and None not in vals:
                    defaults[k] = vals.pop()
            if defaults:
                meta["mac_dinh"] = defaults

        # retrieval text: what is priced this way, and how
        text = (f"Công thức đơn giá cho: {'; '.join(names[:_MAX_NAMES])}. "
                f"Đơn vị: {meta.get('don_vi', '')}. Cách tính: {best}")
        out.append({"text": text, "meta": meta, "source": KIND, "priced": False})
    return out


def is_formula(doc: dict) -> bool:
    return doc.get("meta", {}).get("kind") == KIND


def formula_map(docs) -> dict[str, dict]:
    """``cong_thuc_id`` -> formula document."""
    return {d["meta"]["id"]: d for d in docs if is_formula(d)}


def prompt_entry(doc: dict) -> dict:
    """What the LLM sees of a formula document."""
    m = doc["meta"]
    entry: dict[str, Any] = {"cach_tinh": m["mo_ta"]}
    if m.get("dinh_nghia"):
        entry["dinh_nghia"] = m["dinh_nghia"]
    entry["can_kich_thuoc"] = m.get("kich_thuoc", [])
    params = {p: m[p] for p in _PARAMS if p in m}
    if params:
        entry["tham_so"] = params
    for k in ("don_vi", "ma_sp", "mac_dinh", "so_lan_dung"):
        if m.get(k):
            entry[k] = m[k]
    entry["vi_du"] = m.get("vi_du", [])
    return entry
