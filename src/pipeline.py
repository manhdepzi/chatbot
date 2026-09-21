"""End-to-end pipeline: input -> RAG -> LLM -> arithmetic -> report."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import config
from .loaders.input_parser import parse_input
from .rag import index as rag_index
from .rag import retriever
from .llm import client as llm
from .llm import prompts
from .pricing import calculator as calc
from .generator import report_builder

log = logging.getLogger("pipeline")

_DIM_KEYS = ("w1", "h1", "w2", "h2", "w3", "h3", "l", "r", "e")


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fill_dims_from_name(item: dict[str, Any]) -> dict[str, Any]:
    """Fill any missing dimension fields from the product name itself.

    Many BOQ inputs embed the geometry in the name (``1800x500L1110``,
    ``Cửa bù khí nan chữ Z 1100x2000``, ...) rather than in a dedicated
    area table. Values already parsed from a spreadsheet area table win.
    """
    extracted = calc.extract_dims_from_name(item.get("ten"))
    for k in _DIM_KEYS:
        if item.get(k) in (None, "") and extracted.get(k) is not None:
            item[k] = extracted[k]
    return item


def _price_item(item: dict[str, Any], index: dict, top_k: int) -> dict[str, Any]:
    """Retrieve golden examples and ask the LLM for pricing metadata."""
    if item.get("is_section"):
        return item

    _fill_dims_from_name(item)

    # 1) exact-match pinning: if this exact product exists in a golden quote,
    #    its fields (incl. an intentionally empty price) are authoritative.
    exact = retriever.exact_match(item["ten"], index)
    if exact is not None:
        meta = exact["meta"]
        for k in ("ma_sp", "vat_lieu", "xuat_xu", "don_vi", "gia_ton", "he_so",
                  "ty_ren", "met_dai_ty", "ghi_chu", "don_gia"):
            if meta.get(k) not in (None, "") and item.get(k) in (None, ""):
                item[k] = meta[k]
        item["don_gia"] = meta.get("don_gia")  # may be None (no price in source)
        item["_exact"] = exact["source"]
        # an exact golden match is authoritative: skip LLM pricing entirely
        # (prevents the model from inventing a price the source left blank)
        area = calc.compute_area(item)
        if area is not None:
            item["area"] = area
        item["thanh_tien"] = calc.thanh_tien(item.get("don_gia"), item.get("khoi_luong"))
        item["_examples"] = [{"source": exact["source"], "score": 1.0,
                              "don_gia": meta.get("don_gia")}]
        return item

    examples = retriever.retrieve(item["ten"], index, top_k)

    msgs = prompts.build_item_prompt(item, examples)
    try:
        out = llm.chat_json(msgs)
    except Exception as e:  # noqa: BLE001
        log.warning("LLM failed for %r: %s", item["ten"][:40], e)
        out = {}

    priced = dict(item)
    for k in ("ma_sp", "vat_lieu", "xuat_xu", "don_vi", "gia_ton", "he_so",
              "ty_ren", "met_dai_ty", "ghi_chu", "don_gia"):
        v = out.get(k)
        # never let a null/empty LLM answer overwrite a value we already have
        if v is not None and v != "":
            priced[k] = v

    # fill from best exact golden match if the LLM returned nothing
    if _num(priced.get("don_gia")) is None and examples:
        best = examples[0]
        if _num(best["meta"].get("don_gia")) is not None:
            priced["don_gia"] = best["meta"].get("don_gia")
        for k in ("ma_sp", "vat_lieu", "xuat_xu", "gia_ton", "he_so", "ty_ren"):
            if priced.get(k) in (None, ""):
                priced[k] = best["meta"].get(k)

    # keep source info for traceability
    priced["_examples"] = [
        {"source": d["source"], "score": d["score"], "don_gia": d["meta"].get("don_gia")}
        for d in examples[:top_k]
    ]

    # area
    area = calc.compute_area(priced)
    if area is not None:
        priced["area"] = area

    # unit price fallback from area * gia_ton * he_so when LLM gave nothing
    if _num(priced.get("don_gia")) is None:
        area = _num(priced.get("area"))
        gia_ton = _num(priced.get("gia_ton"))
        he_so = _num(priced.get("he_so"))
        if area is not None and gia_ton is not None:
            priced["don_gia"] = area * gia_ton * (he_so or 1.0)

    # amount
    priced["thanh_tien"] = calc.thanh_tien(priced.get("don_gia"), priced.get("khoi_luong"))
    return priced


def process_file(path: str | Path, index: dict | None = None, force: bool = False,
                 top_k: int | None = None) -> str | None:
    """Process one BOQ file; returns the output path or None if skipped."""
    path = Path(path)
    top_k = top_k or config.RAG_TOP_K
    out_path = config.REPORT_DIR / (path.stem + ".xlsx")

    if out_path.exists() and not force:
        log.info("skip (already exists): %s", out_path.name)
        return None

    if index is None:
        index = rag_index.ensure()

    parsed = parse_input(path)
    items = parsed["items"]
    if not items:
        log.warning("no items parsed from %s", path.name)
        return None

    priced_items = [_price_item(it, index, top_k) for it in items]

    # strip internal example traces before writing
    clean = []
    for it in priced_items:
        d = {k: v for k, v in it.items() if not k.startswith("_")}
        clean.append(d)

    tot = calc.totals(clean)
    out = report_builder.build_report(clean, parsed["header"], out_path, tot)

    # persist state (input file -> output, totals) for idempotency/debug
    _save_state(path, out, tot, priced_items)
    log.info("generated %s (pre-tax=%.0f)", out_path.name, tot["truoc_thue"])
    return out


def _save_state(path: Path, out: str, totals: dict, items: list[dict]):
    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    state = {}
    if config.STATE_FILE.exists():
        state = json.loads(config.STATE_FILE.read_text())
    state[path.name] = {
        "output": out,
        "totals": {k: round(v, 2) for k, v in totals.items()},
        "items": [
            {"ten": it["ten"], "don_gia": it.get("don_gia"),
             "khoi_luong": it.get("khoi_luong"), "thanh_tien": it.get("thanh_tien"),
             "ma_sp": it.get("ma_sp"), "examples": it.get("_examples")}
            for it in items if not it.get("is_section")
        ],
    }
    config.STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def process_all(force: bool = False, top_k: int | None = None) -> list[str]:
    """Process every supported file in data/; returns list of output paths."""
    index = rag_index.ensure()
    outputs = []
    for p in sorted(config.DATA_DIR.iterdir()):
        if p.suffix.lower() not in config.INPUT_EXTS:
            continue
        if p.name.startswith(config.IGNORE_PREFIXES):
            continue
        try:
            out = process_file(p, index=index, force=force, top_k=top_k)
            if out:
                outputs.append(out)
        except Exception as e:  # noqa: BLE001
            log.exception("failed on %s: %s", p.name, e)
    return outputs
