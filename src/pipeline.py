"""End-to-end pipeline.

    data/<file>
      1. READ : full content of every sheet -> OpenAI -> quote header + items
                (canonical name, product type, unit, quantity, sizes)
      2. RAG  : ChromaDB golden rows for each item (exact name + product code
                + embedding similarity)
      3. FILL : items + their golden knowledge + the Mã SP legend learned from
                the knowledge base -> OpenAI -> Mã SP, material, origin, note,
                sheet price, factor, unit price
      4. pin exact golden matches, deterministic arithmetic (area, amounts)
      5. fill the template (incl. the Mã SP legend of the codes used)
         -> reports/<file>.xlsx

Large files are sent in chunks (rows for READ, items for FILL) so prompts and
answers stay within model limits; chunks run in parallel.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from openpyxl.utils import get_column_letter

from . import config
from .loaders.excel_reader import read_excel
from .loaders.input_parser import parse_header, parse_input
from .rag import index as rag_index
from .rag import formulas as fdocs
from .rag import masp as masp_rules
from .rag.masp import code_examples
from .rag import retriever
from .llm import client as llm
from .llm import prompts
from .pricing import calculator as calc
from .pricing import formula as fx
from .generator import report_builder

log = logging.getLogger("pipeline")

_DIM_KEYS = prompts.DIM_FIELDS
_NUM_KEYS = _DIM_KEYS + ("khoi_luong", "gia_ton", "he_so", "don_gia")
# fields an exact golden match is authoritative for
_GOLDEN_KEYS = ("vat_lieu", "xuat_xu", "don_vi", "gia_ton", "he_so", "ghi_chu")
# golden fields shown to the LLM as knowledge
_KNOWLEDGE_KEYS = ("ma_sp", "vat_lieu", "xuat_xu", "don_vi", "don_gia", "gia_ton",
                   "he_so", "ghi_chu") + _DIM_KEYS + ("area",)
# recipe parameters taken from the golden row the formula comes from
_RECIPE_PARAMS = ("gia_ton", "he_so")
# rows before the first item that are repeated as context in later chunks
_CONTEXT_ROWS = 20

_UNIT_WORDS = {
    "m2", "m3", "m", "md", "lm", "set", "cái", "bộ", "kg", "tấn", "chiếc",
    "hộp", "tấm", "cụm", "hệ", "lô", "quả", "cặp", "đôi", "cuộn", "thanh", "cây",
}

_VIET_CHARS = set(
    "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợ"
    "ùúủũụưừứửữựỳýỷỹỵđ"
)


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip().replace(",", "")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_SIZE_TEXT = re.compile(r"\b\d+(?:[.,]\d+)?\s*[xX×]\s*\d+(?:[.,]\d+)?\b|\b[DdØ]\s?\d{2,4}\b")


def _norm_size(s: str) -> str:
    return re.sub(r"\s+", "", s).lower().replace("×", "x")


def _numbers(s: str) -> set[str]:
    return {n.replace(",", ".").lstrip("0") for n in re.findall(r"\d+(?:[.,]\d+)?", s)}


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str)
                         and v.strip().lower() in ("", "null", "none", "n/a"))


def _has_vietnamese(s: str) -> bool:
    return any(ch.lower() in _VIET_CHARS for ch in s)


def _split_vn_name(name: str) -> str:
    """Prefer the Vietnamese line of a multi-language name."""
    parts = [p.strip() for p in name.split("\n") if p.strip()]
    for p in parts:
        if _has_vietnamese(p):
            return p
    return parts[-1] if parts else name


def _batches(seq: list, size: int) -> list[list]:
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def _parallel(fn, jobs: list) -> list:
    with ThreadPoolExecutor(max_workers=config.LLM_WORKERS) as ex:
        return list(ex.map(fn, jobs))


# --- reading the file -------------------------------------------------------

def _fmt(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _row_query(cells: list[tuple[str, Any]]) -> str | None:
    """Product-name text of a row that looks like an item (text + a number).

    The name column comes before material/origin/note columns in every BOQ
    layout, so the first real text cell is taken.
    """
    texts = [str(v).strip() for _, v in cells
             if isinstance(v, str) and len(v.strip()) >= 3
             and _num(v) is None and v.strip().lower() not in _UNIT_WORDS]
    has_number = any(_num(v) is not None for _, v in cells)
    if not texts or not has_number:
        return None
    return texts[0]


def _read_rows(path: Path) -> tuple[list[dict], list[dict]]:
    """Every non-empty row as ``{sheet, ref, text, query}``."""
    sheets = read_excel(path)
    rows: list[dict] = []
    for sh in sheets:
        for i, row in enumerate(sh["grid"]):
            cells = [(get_column_letter(j + 1), v) for j, v in enumerate(row)
                     if not _blank(v)]
            if not cells:
                continue
            rows.append({
                "sheet": sh["name"],
                "ref": f"{sh['name']}!R{i + 1}",
                "text": " | ".join(f"{c}={_fmt(v)}" for c, v in cells),
                "query": _row_query(cells),
            })
    return sheets, rows


def _chunks(rows: list[dict], max_rows: int) -> list[dict]:
    """Split rows into chunks; later chunks of a sheet get its heading rows."""
    heading: dict[str, list[dict]] = {}
    started: set[str] = set()
    for r in rows:
        if r.get("query"):
            started.add(r["sheet"])
        h = heading.setdefault(r["sheet"], [])
        if r["sheet"] not in started and len(h) < _CONTEXT_ROWS:
            h.append(r)

    chunks = [{"rows": b} for b in _batches(rows, max_rows)]
    seen_sheets: set[str] = set()
    for ch in chunks:
        first_sheet = ch["rows"][0]["sheet"]
        in_chunk = {r["ref"] for r in ch["rows"]}
        ch["context"] = ([r for r in heading.get(first_sheet, []) if r["ref"] not in in_chunk]
                         if first_sheet in seen_sheets else [])
        seen_sheets.update(r["sheet"] for r in ch["rows"])
    return chunks


def _serialize_rows(chunk: dict) -> str:
    lines: list[str] = []
    sheet = None
    for tag, rows in (("[ngữ cảnh] ", chunk["context"]), ("", chunk["rows"])):
        for r in rows:
            if r["sheet"] != sheet:
                sheet = r["sheet"]
                lines.append(f"=== Sheet: {sheet} ===")
            lines.append(f"{tag}{r['ref']}: {r['text']}")
    return "\n".join(lines)


# --- step 1: READ the whole file with the LLM -------------------------------------

def _clean_read_item(raw: Any, rows_by_ref: dict[str, dict]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    ten = str(raw.get("ten") or "").strip()
    if _blank(ten):
        return None
    dong = str(raw.get("dong") or "")
    sheet = dong.rsplit("!", 1)[0] if "!" in dong else None
    if raw.get("is_section"):
        return {"is_section": True, "ten": ten, "_sheet": sheet}

    stt = _num(raw.get("stt"))
    item: dict[str, Any] = {"is_section": False, "stt": int(stt) if stt is not None else None,
                            "ten": ten, "_sheet": sheet}
    for k in ("loai", "don_vi") + _DIM_KEYS + ("khoi_luong",):
        v = _num(raw.get(k)) if k in _NUM_KEYS else raw.get(k)
        if not _blank(v):
            item[k] = v.strip() if isinstance(v, str) else v

    row = rows_by_ref.get(dong)
    if row is not None:
        item["_row_text"] = row["text"]
    if row is not None and row.get("query"):
        item["ten_goc"] = row["query"]
        # the name must describe its own row: every size/number in it has to
        # come from that row
        if not _numbers(ten) <= _numbers(row["text"]):
            log.warning("LLM renamed %r -> %r; keeping the input name",
                        row["query"][:40], ten[:40])
            item["ten"] = _split_vn_name(row["query"])
        # ... and keep the sizes the row states (formulas need them)
        for size in dict.fromkeys(_SIZE_TEXT.findall(row["query"])):
            if _norm_size(size) not in _norm_size(item["ten"]):
                item["ten"] = f"{item['ten']} {size}"
    return item


def _dedupe_across_sheets(items: list[dict]) -> list[dict]:
    """Merge items that another sheet already listed (same name + quantity).

    Workbooks often hold the same list twice (summary sheet + quotation or
    area sheet). One LLM call merges them itself, but chunks of a large file
    cannot see each other. Repeats within one sheet are kept: the same product
    legitimately appears under several sections.
    """
    seen: dict[tuple, dict] = {}
    out: list[dict] = []
    for it in items:
        if it.get("is_section"):
            out.append(it)
            continue
        key = (" ".join(str(it.get("ten_goc") or it["ten"]).split()).lower(),
               it.get("khoi_luong"))
        prev = seen.get(key)
        if prev is not None and prev.get("_sheet") != it.get("_sheet"):
            for k, v in it.items():
                if _blank(prev.get(k)) and not _blank(v):
                    prev[k] = v
            continue
        seen.setdefault(key, it)
        out.append(it)

    # sections of a sheet whose items were all merged away
    kept = {it.get("_sheet") for it in out if not it.get("is_section")}
    return [it for it in out
            if not (it.get("is_section") and it.get("_sheet") and it["_sheet"] not in kept)]


def _read_chunk(file_name: str, chunk: dict,
                rows_by_ref: dict[str, dict]) -> tuple[dict, list[dict]]:
    out = llm.chat_json(prompts.build_read_prompt(file_name, _serialize_rows(chunk)))
    header = out.get("header") if isinstance(out.get("header"), dict) else {}
    items = [it for it in (_clean_read_item(x, rows_by_ref)
                           for x in (out.get("items") or [])) if it]
    return header, items


def _read_with_llm(path: Path, rows: list[dict]) -> tuple[dict, list[dict]]:
    chunks = _chunks(rows, config.CHUNK_ROWS)
    rows_by_ref = {r["ref"]: r for r in rows}
    log.info("%s: READ %d rows in %d LLM call(s)", path.name, len(rows), len(chunks))
    results = _parallel(lambda ch: _read_chunk(path.name, ch, rows_by_ref), chunks)

    header: dict[str, Any] = {}
    items: list[dict] = []
    for h, its in results:
        for k, v in h.items():
            if k in prompts.HEADER_FIELDS and not _blank(v) and k not in header:
                header[k] = str(v).strip()
        items.extend(its)
    return header, _dedupe_across_sheets(items)


def _read_fallback(path: Path) -> tuple[dict, list[dict]]:
    """Heuristic parser, used when the READ step fails."""
    parsed = parse_input(path)
    for it in parsed["items"]:
        if not it.get("is_section"):
            it["ten_goc"] = it["ten"]
            it["ten"] = _split_vn_name(it["ten"])
    return parsed["header"], parsed["items"]


# --- step 2: look the products up in the database ------------------------------------

def _lookup_db(items: list[dict], index: dict, exact_map: dict, top_k: int) -> None:
    """For every product: the golden row with the same name (if any) and the
    related golden rows and price formulas, the exact row first."""
    targets = [it for it in items if not it.get("is_section")]
    if not targets:
        return
    for it in targets:
        it["_exact"] = (retriever.exact_match(it.get("ten_goc"), exact_map)
                        or retriever.exact_match(it.get("ten"), exact_map))
    found = retriever.retrieve_knowledge([it["ten"] for it in targets], index, top_k)
    for it, (docs, formulas) in zip(targets, found):
        exact = it["_exact"]
        if exact is not None:
            docs = [dict(exact, score=1.0, _exact=True)] + \
                [d for d in docs if d["text"] != exact["text"]][:top_k - 1]
        it["_similar"] = docs
        it["_similar_formulas"] = [retriever.formula_of(d, index) for d in docs]
        it["_formulas"] = formulas


def _knowledge_entry(doc: dict) -> dict:
    m = doc.get("meta", {})
    entry = {"ten": doc["text"]}
    for k in _KNOWLEDGE_KEYS:
        if not _blank(m.get(k)):
            entry[k] = m[k]
    entry["nguon"] = doc.get("source", "").split(":")[0]
    return entry


# --- step 3: the LLM decides the results from the database knowledge -----------------

def _serialize_items(batch: list[tuple[int, dict]]) -> tuple[str, dict[str, dict]]:
    """The batch's formula set, then the items, each followed by its golden
    knowledge (``K<n>``) and the formulas that fit it (``F<n>``)."""
    fids: dict[str, str] = {}      # formula document id -> F<n>
    by_id: dict[str, dict] = {}    # K<n> / F<n> -> document
    formula_lines: list[str] = []

    def fid(doc: dict) -> str:
        key = doc["meta"]["id"]
        if key not in fids:
            fids[key] = f"F{len(fids) + 1}"
            by_id[fids[key]] = doc
            entry = json.dumps(fdocs.prompt_entry(doc), ensure_ascii=False, default=str)
            formula_lines.append(f"{fids[key]}: {entry}")
        return fids[key]

    lines: list[str] = []
    for i, it in batch:
        info = {k: it[k] for k in ("ten", "loai", "don_vi", "khoi_luong")
                if not _blank(it.get(k))}
        if it.get("ten_goc") and it["ten_goc"] != it["ten"]:
            info["dong_goc"] = it["ten_goc"]
        if it.get("_dims_hint"):
            info["kt_doc_tu_ten"] = it["_dims_hint"]
        lines.append(f"#{i}: {json.dumps(info, ensure_ascii=False, default=str)}")
        for doc, f in zip(it.get("_similar", []), it.get("_similar_formulas", [])):
            kid = f"K{len(by_id) + 1}"
            by_id[kid] = doc
            entry = _knowledge_entry(doc)
            if f is not None:
                entry["cong_thuc"] = fid(f)
            label = "khớp nguyên văn" if doc.get("_exact") else f"tương tự {doc['score']:.2f}"
            lines.append(f"    ↳ {kid} [{label}]: "
                         f"{json.dumps(entry, ensure_ascii=False, default=str)}")
        if not it.get("_similar"):
            lines.append("    ↳ (không có sản phẩm tương tự)")
        cands = [fid(f) for f in it.get("_formulas", [])]
        lines.append(f"    ⚙ công thức có thể dùng: {', '.join(dict.fromkeys(cands)) or '(không có)'}")

    content = ("BỘ CÔNG THỨC (từ file Excel báo giá vàng, đã kiểm chứng):\n"
               + ("\n".join(formula_lines) or "(không có)")
               + "\n\nSẢN PHẨM:\n" + "\n".join(lines))
    return content, by_id


def _sizes(text: str) -> set[str]:
    return {_norm_size(x) for x in _SIZE_TEXT.findall(text or "")}


def _same_spec(item: dict, doc: dict) -> bool:
    """A golden row describes the same specification as the item: the same
    sizes (D150 is not 150x150), the same product code, or — for a name with
    no size — the same numbers (thickness...). A name with nothing to compare
    only matches exactly (handled before FILL)."""
    if doc.get("_exact"):
        return True
    names = " ".join(str(item.get(k) or "") for k in ("ten", "ten_goc"))
    text = doc.get("text", "")
    code = retriever.product_code(names)
    if code and code not in text:
        return False
    sizes = _sizes(names)
    if sizes:
        return sizes <= _sizes(text)
    nums = _numbers(names)
    return bool(nums) and nums <= _numbers(text)


def _from_golden(field: str, value: Any, item: dict) -> bool:
    """True when ``value`` is what one of the item's golden rows holds for
    ``field`` — copied, not made up. A price
    must moreover come from a row of the same specification."""
    docs = item.get("_similar", [])
    for d in docs:
        g = d.get("meta", {}).get(field)
        if _blank(g):
            continue
        if field == "don_gia":
            if _num(g) is not None and fx.close(_num(g), _num(value)) and _same_spec(item, d):
                return True
        elif " ".join(str(g).split()).lower() == " ".join(str(value).split()).lower():
            return True
    return False


def _num_key(v: Any) -> str:
    return f"{float(v):g}".lstrip("0")


def _size_ok(item: dict, field: str, value: float) -> bool:
    """A size must be written in the product's own name / row, or be what a
    golden row of the item gives that same column (a convention such as
    R=150 on 300x300 elbows) — never a made-up number."""
    text = " ".join(str(item.get(k) or "") for k in ("ten", "ten_goc", "_row_text"))
    if _num_key(value) in _numbers(text):
        return True
    # a convention (not written in the name) only from the same product: the
    # exact golden row, or a golden row with the same main section W1 x H1
    hint = item.get("_dims_hint") or {}
    main = (_num(hint.get("w1")), _num(hint.get("h1")))
    for d in item.get("_similar", []):
        m = d.get("meta", {})
        same = d.get("_exact") or (None not in main and
                                  (_num(m.get("w1")), _num(m.get("h1"))) == main)
        if same and _num(m.get(field)) is not None and fx.close(_num(m[field]), value):
            return True
    return False


def _decide_batch(project: str, batch: list[tuple[int, dict]],
                  catalog: list[tuple[str, str]], examples: dict[str, list[str]]) -> None:
    content, by_id = _serialize_items(batch)
    try:
        out = llm.chat_json(prompts.build_result_prompt(project, catalog, examples, content))
    except Exception as e:  # noqa: BLE001
        log.warning("RESULT failed for %d item(s): %s", len(batch), e)
        return
    codes = {c for _, c in catalog}
    items = dict(batch)
    for raw in out.get("items") or []:
        if not isinstance(raw, dict):
            continue
        i = _num(raw.get("i"))
        it = items.get(int(i)) if i is not None else None
        if it is None:
            continue
        it["_answered"] = True
        # Mã SP: one of the legend's codes, or none
        code = str(raw.get("ma_sp") or "").strip().lower()
        it["_ma_sp_llm"] = code if code in codes else ""
        # area-table sizes, each checked against the name and the golden rows
        for k in _DIM_KEYS:
            v = _num(raw.get(k))
            if v is None:
                continue
            if _size_ok(it, k, v):
                it[k] = v
            else:
                log.info("drop %s=%r for %r: not in the name or golden data",
                         k, v, it["ten"][:40])
        for k in prompts.RESULT_TEXT_FIELDS:
            v = _num(raw.get(k)) if k in _NUM_KEYS else raw.get(k)
            if _blank(v) or not _blank(it.get(k)):
                continue
            # the LLM only picks: a value must be one a golden row holds
            if not _from_golden(k, v, it):
                log.info("drop %s=%r for %r: not in golden data", k, v, it["ten"][:40])
                continue
            it[k] = v.strip() if isinstance(v, str) else v
        refs = [k for k in (raw.get("tham_chieu") or []) if k in by_id and k.startswith("K")]
        it["_refs"] = [
            {"source": by_id[k]["source"], "ten": by_id[k]["text"],
             "don_gia": by_id[k]["meta"].get("don_gia")}
            for k in refs
        ]
        # the formula the LLM chose from the formula set
        k = raw.get("cong_thuc_tu")
        if k in by_id and fdocs.is_formula(by_id[k]):
            it["_recipe_doc"] = by_id[k]


def _decide_with_llm(path: Path, header: dict, items: list[dict],
                     catalog: list[tuple[str, str]], examples: dict[str, list[str]]) -> None:
    targets = [(i, it) for i, it in enumerate(items) if not it.get("is_section")]
    if not targets:
        return
    batches = _batches(targets, config.RESULT_BATCH)
    log.info("%s: RESULT for %d product(s) in %d LLM call(s)",
             path.name, len(targets), len(batches))
    project = header.get("du_an") or path.stem
    _parallel(lambda b: _decide_batch(project, b, catalog, examples), batches)


# --- step 4: arithmetic -------------------------------------------------------------

def _take_dim_hints(item: dict) -> None:
    """Sizes read from the name (by READ and by pattern) become hints for the
    RESULT step, which decides the area table the way golden fills it."""
    hints = {k: item.pop(k) for k in _DIM_KEYS if not _blank(item.get(k))}
    for name in (item.get("ten"), item.get("ten_goc")):
        for k, v in calc.extract_dims_from_name(name).items():
            if k in _DIM_KEYS:
                hints.setdefault(k, v)
    item["_dims_hint"] = hints


def _normalize_ma_sp(item: dict, codes: dict[str, str]) -> None:
    """Keep only a Mã SP from the learned legend (in its canonical case).

    Golden area tables leave Mã SP empty for anything that is not a sheet-metal
    duct/fitting (grilles, dampers, louvers...), so an unknown code is dropped.
    """
    m = str(item.get("ma_sp") or "").strip().lower()
    if m in codes:
        item["ma_sp"] = codes[m]
    elif codes:
        item.pop("ma_sp", None)


_VAN_L = 200.0        # van body length when the name gives none (VCD, MD, NRD, PRD)
_FIRE_VAN_L = 250.0   # ... for a fire damper (MFD, FD), as golden builds them
_CON_L = 500.0        # reducer length when the name gives none
_VAN_LEAD = re.compile(r"^(?:(?:cung cấp|lắp đặt|và|,)\s*)*(?:van|valve)\b"
                       r"|^(?:[\w-]+\s+){0,3}damper\b")
_FIRE_VAN = re.compile(r"\bm?fs?d\b|ngăn cháy|chặn lửa|fire damper", re.I)
_NOT_FIRE_VAN = re.compile(r"\bnrd\b|\bprd\b|một chiều|1 chiều|non.?return|check valve"
                           r"|xả áp|giảm áp|relief", re.I)


def _is_van(item: dict) -> bool:
    """A damper/valve (the product itself, not a grille "kèm van OBD")."""
    if str(item.get("loai") or "").strip().lower().startswith("van"):
        return True
    name = _split_vn_name(str(item.get("ten") or "")).strip().lower()
    return bool(_VAN_LEAD.match(name))


def _area_conventions(item: dict, codes: set[str]) -> None:
    """Area-table sizes that names leave out.

    A rectangular van is measured as an end cap (tb): side walls + section,
    2x(W+H)xL + WxH, L being its body length — golden van sheets keep that
    length in W2 and helper sums in H2..H3 — when unknown 250 for a fire
    damper (MFD/FD), 200 for the others. A reducer (g) without a length is L500.
    """
    names = " ".join(str(item.get(k) or "") for k in ("ten", "ten_goc"))
    rectangular = re.search(r"\d\s*[xX×*]\s*\d", names) is not None  # not D150
    if _is_van(item) and rectangular and "tb" in codes \
            and None not in (_num(item.get("w1")), _num(item.get("h1"))):
        fire = _FIRE_VAN.search(names) and not _NOT_FIRE_VAN.search(names)
        body = _num(item.get("l")) or _num(item.get("w2")) \
            or (_FIRE_VAN_L if fire else _VAN_L)
        for k in ("w2", "h2", "w3", "h3"):
            item.pop(k, None)
        item["ma_sp"] = "tb"
        item["l"] = body
    elif str(item.get("ma_sp") or "").lower() == "g" and _num(item.get("l")) is None:
        item["l"] = _CON_L


def masp_legend(items: list[dict], catalog: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Legend rows for the report: the learned (type, code) pairs whose code
    is used in this report's area table, in the golden legend order."""
    used = {str(it.get("ma_sp") or "").lower() for it in items if not it.get("is_section")}
    return [(label, code) for label, code in catalog if code in used]


_SIZE_VARS = set(_DIM_KEYS) | {"area"}


def _unit(d: dict) -> str:
    """Unit without its translation line ("cái\n个" -> "cái")."""
    return str(d.get("don_vi") or "").split("\n")[0].split("/")[0].strip().lower()


def _recipe_value(item: dict, doc: dict) -> tuple[float, dict[str, Any]] | None:
    """Price ``item`` with the verified Excel formula of golden ``doc`` (a
    formula document or a golden row).

    Sizes come from the item (a size the formula needs but the item lacks
    makes the formula inapplicable); price parameters (giá tôn, hệ số — in
    some sheets frame/blade unit prices) come with the formula, blank meaning
    0 exactly as in Excel. An ``[area]`` formula measures the item the way its
    golden rows were measured: their shape code and fixed sizes (``mac_dinh``)
    when the item has none. Returns ``(price, updates)`` — the field values
    the price was computed from, to be written into the report row.
    """
    meta = doc["meta"]
    recipe = meta.get("cong_thuc")
    if not recipe:
        return None
    try:
        node = fx.parse(recipe)
    except fx.FormulaError:
        return None
    names = fx.variables(node)
    # a formula without any size is a fixed price: only for the same unit
    if not names & _SIZE_VARS and _unit(item) != _unit(meta):
        return None

    updates: dict[str, Any] = {}
    van = _is_van(item)
    if "area" in names:
        work = {k: v for k, v in item.items() if k != "area"}
        if _blank(work.get("ma_sp")) and meta.get("ma_sp"):
            # measure like the formula's golden rows (e.g. dampers as tb)
            updates["ma_sp"] = work["ma_sp"] = meta["ma_sp"]
        # a golden row's own sizes, or the sizes fixed across a formula's rows
        defaults = (meta.get("mac_dinh") or {}) if fdocs.is_formula(doc) else \
            {k: meta[k] for k in fdocs.DEFAULTABLE if _num(meta.get(k)) is not None}
        if van:
            # golden van sheets hold helper sums there, not sizes
            defaults = {k: v for k, v in defaults.items() if k not in ("w2", "h2", "w3", "h3")}
        for k, v in defaults.items():
            if _blank(work.get(k)):
                updates[k] = work[k] = v
        area = calc.compute_area(work)
        if area is None:
            return None
        updates["area"] = area

    env: dict[str, Any] = {}
    for name in names:
        if name in _RECIPE_PARAMS:
            v = _num(meta.get(name)) or 0.0
            updates[name] = v
        elif name in updates:
            v = updates[name]
        elif name == "ma_sp":
            v = item.get("ma_sp")
        else:
            v = _num(item.get(name))
            if v is None and name == "w2" and van:
                # golden van sheets keep the body length in W2
                v = _num(item.get("l"))
            if v is None:
                return None
        env[name] = v
    try:
        value = _num(fx.evaluate(node, lambda n: env[n[1]]))
    except (fx.FormulaError, KeyError, ZeroDivisionError, OverflowError, ValueError):
        return None
    return (value, updates) if value is not None else None


def _use_recipe(item: dict, doc: dict, value: float, updates: dict[str, Any]) -> None:
    item["don_gia"] = value
    item["cong_thuc"] = doc["meta"]["cong_thuc"]
    item["_recipe_source"] = doc["meta"].get("id") or doc["source"]
    item.update(updates)


def _masp_supported(item: dict, code: str, masp) -> bool:
    """The LLM's Mã SP has a basis: the legend label the name starts with,
    golden's coding of names starting that way, or a golden row of the same
    product carrying it."""
    name = item.get("ten")
    if masp_rules.by_label(name, masp.catalog) == code:
        return True
    if masp_rules.lead_supports(masp, name, code):
        return True
    return any(str(d.get("meta", {}).get("ma_sp") or "").lower() == code
               and (d.get("_exact") or _same_spec(item, d))
               for d in item.get("_similar", []))


def _finalize(item: dict, catalog: list[tuple[str, str]], masp) -> dict:
    if item.get("is_section"):
        return item

    if not item.get("_answered"):
        # no RESULT answer for this product: the sizes read from its name
        for k, v in (item.get("_dims_hint") or {}).items():
            item.setdefault(k, v)
    # an exact golden match is authoritative, including an empty price; its
    # area-table sizes are how golden filled this very product
    exact = item.get("_exact")
    if exact is not None:
        meta = exact["meta"]
        for k in _DIM_KEYS:
            if _blank(item.get(k)) and _num(meta.get(k)) is not None:
                item[k] = meta[k]
        for k in _GOLDEN_KEYS:
            if not _blank(meta.get(k)):
                item[k] = meta[k]
        item["don_gia"] = meta.get("don_gia")
        item["_refs"] = [{"source": exact["source"], "ten": exact["text"],
                          "don_gia": meta.get("don_gia")}]

    # Mã SP: the broad class relative to the legend (LLM step, guided by golden
    # examples); the golden statistics only stand in when that step failed
    code = item.get("_ma_sp_llm")
    if code and not _masp_supported(item, code, masp):
        log.info("drop ma_sp=%r for %r: no support in the legend or golden data",
                 code, item["ten"][:40])
        code = None
    if code is None:
        code = masp.classify(item.get("ten"), item.get("ten_goc"))
    if code:
        item["ma_sp"] = code
    else:
        item.pop("ma_sp", None)
    _normalize_ma_sp(item, {code: code for _, code in catalog})
    _area_conventions(item, {code for _, code in catalog})
    # the template's DIỆN TÍCH /CÁI formula, nothing else
    area = calc.compute_area(item)
    if area is not None:
        item["area"] = area

    # 1) exact golden row: its price; its formula too if it reproduces it
    # 2) the verified Excel formula of the chosen same-type golden row
    # 3) a price copied verbatim from a golden row (checked in FILL)
    # nothing is computed without a golden formula
    recipe_doc = exact if exact is not None else item.get("_recipe_doc")
    applied = _recipe_value(item, recipe_doc) if recipe_doc is not None else None
    if exact is not None:
        if applied is not None and fx.close(applied[0], item["don_gia"]):
            _use_recipe(item, exact, *applied)
    elif applied is not None:
        _use_recipe(item, recipe_doc, *applied)

    item["thanh_tien"] = calc.thanh_tien(item.get("don_gia"), item.get("khoi_luong"))
    return item


# --- orchestration ------------------------------------------------------------------

def _is_up_to_date(src: Path, out: Path) -> bool:
    return out.exists() and out.stat().st_mtime >= src.stat().st_mtime


def process_file(path: str | Path, index: dict | None = None, force: bool = False,
                 top_k: int | None = None) -> str | None:
    """Process one BOQ file; returns the output path or None if skipped."""
    path = Path(path)
    top_k = top_k or config.RAG_TOP_K
    out_path = config.REPORT_DIR / (path.stem + ".xlsx")

    if not force and _is_up_to_date(path, out_path):
        log.info("skip (report is up to date): %s", out_path.name)
        return None

    if index is None:
        index = rag_index.ensure()

    # 1. READ
    sheets, rows = _read_rows(path)
    try:
        header, items = _read_with_llm(path, rows)
        if not any(not it.get("is_section") for it in items):
            raise RuntimeError("LLM returned no items")
    except Exception as e:  # noqa: BLE001
        log.warning("READ failed for %s (%s); using heuristic parser", path.name, e)
        header, items = _read_fallback(path)
    if not any(not it.get("is_section") for it in items):
        log.warning("no items found in %s", path.name)
        return None

    # labelled header cells found without the LLM fill any remaining gaps
    for k, v in parse_header(sheets).items():
        header.setdefault(k, v)
    header.setdefault("ngay_bg", _dt.date.today().strftime("%d/%m/%Y"))

    # 2. DATABASE: what golden data holds for each identified product
    for it in items:
        if not it.get("is_section"):
            _take_dim_hints(it)
    catalog = retriever.masp_catalog(index)
    try:
        _lookup_db(items, index, retriever.build_exact_map(index), top_k)
    except Exception as e:  # noqa: BLE001
        log.warning("database lookup failed for %s: %s", path.name, e)

    # 3. RESULT: the LLM decides every field from that knowledge
    _decide_with_llm(path, header, items, catalog,
                     code_examples(retriever.masp_model(index)))

    # 4. checks + arithmetic, 5. report
    masp = retriever.masp_model(index)
    items = [_finalize(it, catalog, masp) for it in items]
    legend = masp_legend(items, catalog)
    clean = [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]
    tot = calc.totals(clean)
    out = report_builder.build_report(clean, header, out_path, tot, legend=legend,
                                      amount_formula=retriever.amount_recipe(index))

    _save_state(path, out, tot, header, items, legend)
    log.info("generated %s (%d items, pre-tax=%.0f)", out_path.name,
             sum(1 for it in items if not it.get("is_section")), tot["truoc_thue"])
    return out


def _save_state(path: Path, out: str, totals: dict, header: dict, items: list[dict],
                legend: list[tuple[str, str]]):
    """Persist input -> output, header and per-item sources for traceability."""
    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    state = {}
    if config.STATE_FILE.exists():
        try:
            state = json.loads(config.STATE_FILE.read_text())
        except json.JSONDecodeError:
            state = {}
    state[path.name] = {
        "output": out,
        "header": header,
        "ma_sp_legend": legend,
        "totals": {k: round(v, 2) for k, v in totals.items()},
        "items": [
            {"ten": it["ten"], "ten_goc": it.get("ten_goc"), "loai": it.get("loai"),
             "ma_sp": it.get("ma_sp"), "don_gia": it.get("don_gia"),
             "khoi_luong": it.get("khoi_luong"), "thanh_tien": it.get("thanh_tien"),
             "exact": (it.get("_exact") or {}).get("source"),
             "cong_thuc": it.get("cong_thuc"), "cong_thuc_nguon": it.get("_recipe_source"),
             "refs": it.get("_refs")}
            for it in items if not it.get("is_section")
        ],
    }
    config.STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str))


def input_files() -> list[Path]:
    """Supported input files currently in data/."""
    if not config.DATA_DIR.exists():
        return []
    return [p for p in sorted(config.DATA_DIR.iterdir())
            if p.is_file() and p.suffix.lower() in config.INPUT_EXTS
            and not p.name.startswith(config.IGNORE_PREFIXES)]


def process_all(force: bool = False, top_k: int | None = None) -> list[str]:
    """Process every supported file in data/; returns list of output paths."""
    index = rag_index.ensure()
    outputs = []
    for p in input_files():
        try:
            out = process_file(p, index=index, force=force, top_k=top_k)
            if out:
                outputs.append(out)
        except Exception as e:  # noqa: BLE001
            log.exception("failed on %s: %s", p.name, e)
    return outputs
