"""Deterministic pricing arithmetic.

The LLM decides *what* a product is and supplies unit price / price factors;
this module does the arithmetic that must be exactly right:

  * per-unit area for every Kaiyo shape — the same formulas the golden quotes
    embed in their ``DIỆN TÍCH /CÁI`` column (transcribed verbatim below)
  * dimension extraction from the product name itself
  * thanh_tien = don_gia * khoi_luong
  * totals: pre-tax, VAT 10%, VAT 8%, post-tax.
"""
from __future__ import annotations

import math
import re
from typing import Any


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


_DIM_PAIR = re.compile(r"(\d+(?:[.,]\d+)?)\s*[xX]\s*(\d+(?:[.,]\d+)?)")
_REDUCER = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[xX]\s*(\d+(?:[.,]\d+)?)"
    r"\s*/\s*(\d+(?:[.,]\d+)?)\s*[xX]\s*(\d+(?:[.,]\d+)?)"
)


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def extract_dims_from_name(name: Any) -> dict[str, float]:
    """Recover W/H/L/R/E dimensions embedded in a product name.

    Kaiyo product names carry their own geometry, e.g.::

        "Ống tôn mạ kẽm 1800x500L1110mm"              -> w1=1800 h1=500 l=1110
        "Cút 90 độ ... KT 1800x500 R500"              -> w1=1800 h1=500 r=500 e=90
        "Côn thu ... KT 1800x500/1500x400L500"        -> w1 h1 w2 h2 l
        "Cửa bù khí nan chữ Z 1100x2000 kèm van OBD"  -> w1=1100 h1=2000
        "600X200, H=300"                              -> w1=600 h1=200 e=300

    A reducer carries two ``WxH`` groups separated by ``/`` (W1/H1 and W2/H2).
    A single ``WxH`` (even when the name repeats it, e.g. a Chinese gloss) is
    only W1/H1. ``L`` is length, ``R`` is radius, an explicit "NN độ" is the
    elbow angle, and "H=NN" is the drop (E column) of a down-up fitting.
    """
    name = "" if name is None else str(name)
    out: dict[str, float] = {}

    reducer = _REDUCER.search(name)
    if reducer:
        out["w1"] = _to_float(reducer.group(1))
        out["h1"] = _to_float(reducer.group(2))
        out["w2"] = _to_float(reducer.group(3))
        out["h2"] = _to_float(reducer.group(4))
    else:
        pair = _DIM_PAIR.search(name)
        if pair:
            out["w1"] = _to_float(pair.group(1))
            out["h1"] = _to_float(pair.group(2))

    m = re.search(r"[Ll]\s*(\d+(?:[.,]\d+)?)", name)
    if m:
        out["l"] = _to_float(m.group(1))

    m = re.search(r"[Rr]\s*(\d+(?:[.,]\d+)?)", name)
    if m:
        out["r"] = _to_float(m.group(1))

    m = re.search(r"(\d+(?:[.,]\d+)?)\s*độ", name)
    if m:
        out["e"] = _to_float(m.group(1))

    m = re.search(r"[Hh]\s*=\s*(\d+(?:[.,]\d+)?)", name)
    if m:
        out["e"] = _to_float(m.group(1))

    return out


def duct_area(w: Any, h: Any, l: Any) -> float | None:
    """Rectangular duct surface area in m^2 (2*(W+H)*L/1e6)."""
    w, h, l = _num(w), _num(h), _num(l)
    if w is None or h is None or l is None:
        return None
    return 2 * (w + h) * l / 1e6


def compute_area(item: dict[str, Any]) -> float | None:
    """Per-unit area in m^2, using the exact formula the golden quotes use.

    Branches are transcribed from the ``DIỆN TÍCH /CÁI`` column of the golden
    workbooks (mã SP -> shape):

      t / TA        duct           2*(W1+H1)*L / 1e6
      F / M         grille         W1*H1 / 1e6
      d             down-up        (W1+H1+E/2)*2*L / 1e6
      tb            end cap        ((W1+H1)*2*L + W1*H1) / 1e6
      g / n         reducer/leg    trapezoid of (W1,W2,H1,H2,L) / 1e6
      c* (cv)       elbow          circular arc of (W1,H1,R,E) / 1e6
      vt            fan cone       reducer + fan-plate ring of (R) / 1e6
      tt            tee            two branch arcs / 1e6

    An explicit ``area`` value from the input wins over the formula.
    """
    if _num(item.get("area")) is not None:
        return _num(item["area"])

    m = (item.get("ma_sp") or "").strip().lower()
    w1, h1 = _num(item.get("w1")), _num(item.get("h1"))
    w2, h2 = _num(item.get("w2")), _num(item.get("h2"))
    w3, h3 = _num(item.get("w3")), _num(item.get("h3"))
    l, r, e = _num(item.get("l")), _num(item.get("r")), _num(item.get("e"))

    if m in ("t", "ta"):
        if None in (w1, h1, l):
            return None
        return (w1 + h1) * 2 / 1000 * l / 1000

    if m in ("f", "m"):
        if None in (w1, h1):
            return None
        return w1 * h1 / 1e6

    if m == "d":
        if None in (w1, h1, l):
            return None
        return (w1 + h1 + (e or 0.0) / 2) * 2 * l / 1e6

    if m == "tb":
        if None in (w1, h1, l):
            return None
        return ((w1 + h1) * 2 * l + w1 * h1) / 1e6

    if m in ("g", "n"):
        if None in (w1, h1, w2, h2, l):
            return None
        return ((w1 + w2) * math.sqrt(l ** 2 + ((h1 - h2) / 2) ** 2)
                + (h1 + h2) * math.sqrt(l ** 2 + ((w1 - w2) / 2) ** 2)) / 1e6

    if m.startswith("c"):  # cv / cút / chếch
        if None in (w1, h1, r, e):
            return None
        return (2 * (math.pi * ((w1 + r) ** 2 - r ** 2)
                     + h1 * (math.pi * (w1 + r) + math.pi * r)) * (e / 360)) / 1e6

    if m.startswith("vt"):
        if None in (w1, h1, w2, h2, l, r):
            return None
        base = ((w1 + w2) * math.sqrt(l ** 2 + ((h1 - h2) / 2) ** 2)
                + (h1 + h2) * math.sqrt(l ** 2 + ((w1 - w2) / 2) ** 2))
        return (base + ((r + 200) ** 2 - (r / 2) ** 2 * 3.14)) / 1e6

    if m == "tt":
        if None in (w1, h1, w2, h2, w3, h3, l):
            return None
        return (2 * (w1 + h1) * (l - 1.5 * w2)
                + (math.pi * (3 * w2 + 2 / 3 * w1) * (w2 + 2 / 3 * w1 + h2 + h1)
                   - (h2 + h1) * (math.pi * 1.5 * w2)) / 8
                + (math.pi * (3 * w3 + 2 / 3 * w1) * (w3 + 2 / 3 * w1 + h3 + h1)
                   - (h3 + h1) * (math.pi * 1.5 * w3)) / 8) / 1e6

    # flat plate / grille / damper that only carries a WxH (no length/radius):
    # same as the golden F/M branch -> W*H/1e6
    if w1 is not None and h1 is not None and l is None and r is None:
        return w1 * h1 / 1e6

    return None


def thanh_tien(don_gia: Any, khoi_luong: Any) -> float | None:
    dg, kl = _num(don_gia), _num(khoi_luong)
    if kl is None:
        return None
    if dg is None:
        # price unknown: source quotations write 0 rather than blank
        return 0.0
    return dg * kl


def totals(items: list[dict[str, Any]]) -> dict[str, float]:
    """Aggregate over items carrying a ``thanh_tien`` value.

    Kaiyo quotes apply the 10% VAT line; the 8% line is a fallback kept at 0.
    """
    pre = sum(_num(it.get("thanh_tien")) or 0.0 for it in items)
    vat10 = pre * 0.10
    vat8 = 0.0
    post = pre + vat10
    return {
        "truoc_thue": pre,
        "vat10": vat10,
        "vat8": vat8,
        "sau_thue": post,
    }
