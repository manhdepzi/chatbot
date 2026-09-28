"""Retrieve golden examples for product names."""
from __future__ import annotations

import re

from . import index as idx
from .formulas import KIND as FORMULA_KIND
from .. import config
from ..llm import client as llm

_EMBED_BATCH = 256

# product codes like EAG, NRD, VCD, FAL, LCCT, MFD ... (2-6 capital letters)
_CODE = re.compile(r"\b([A-Z]{2,6})\b")
_NOT_CODES = {"KT", "EI", "OBD", "DN", "PVC", "EA", "SET", "HT"}


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


def product_code(name: str) -> str | None:
    """First product-code token in a name (used as a keyword filter)."""
    for m in _CODE.finditer(name or ""):
        if m.group(1) not in _NOT_CODES:
            return m.group(1)
    return None


def _embed_all(names: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for i in range(0, len(names), _EMBED_BATCH):
        vectors.extend(llm.embed(names[i:i + _EMBED_BATCH]))
    return vectors


def _hybrid(index: dict, name: str, vec: list[float], k: int, **filters) -> list[dict]:
    """Documents carrying the same product code (EAG, NRD, ...) first, then
    the nearest ones by embedding."""
    docs: list[dict] = []
    code = product_code(name)
    if code:
        docs = idx.search(index, vec, k, contains=code, **filters)
    seen = {d["text"] for d in docs}
    for d in idx.search(index, vec, k, **filters):
        if len(docs) >= k:
            break
        if d["text"] not in seen:
            docs.append(d)
            seen.add(d["text"])
    return docs


def retrieve_many(names: list[str], index: dict,
                  top_k: int | None = None) -> list[list[dict]]:
    """Top-k priced golden product rows for each name (embedded in batches)."""
    top_k = top_k or config.RAG_TOP_K
    return [_hybrid(index, n, v, top_k, priced_only=True)
            for n, v in zip(names, _embed_all(names))]


def formula_of(doc: dict, index: dict) -> dict | None:
    """Formula document a golden product row was priced with."""
    return formula_lookup(index).get(doc.get("meta", {}).get("cong_thuc_id"))


def retrieve_knowledge(names: list[str], index: dict, top_k: int | None = None,
                       formula_k: int | None = None) -> list[tuple[list[dict], list[dict]]]:
    """For each name: (similar golden product rows, candidate price formulas).

    Formulas come first from the similar rows themselves (the method those
    products were priced with), then from a search of the formula documents.
    """
    top_k = top_k or config.RAG_TOP_K
    formula_k = formula_k or config.FORMULA_TOP_K
    fmap = formula_lookup(index)
    out = []
    for name, vec in zip(names, _embed_all(names)):
        products = _hybrid(index, name, vec, top_k, priced_only=True)
        formulas: list[dict] = []
        for d in products:
            f = fmap.get(d["meta"].get("cong_thuc_id"))
            if f is not None and f not in formulas:
                formulas.append(f)
        for f in _hybrid(index, name, vec, formula_k, where={"kind": FORMULA_KIND}):
            full = fmap.get(f["meta"].get("id"), f)
            if full not in formulas:
                formulas.append(full)
        out.append((products, formulas[:top_k + formula_k]))
    return out


def formula_lookup(index: dict) -> dict[str, dict]:
    """``cong_thuc_id`` -> formula document (cached on the index handle)."""
    if "_formulas" not in index:
        index["_formulas"] = {d["meta"]["id"]: d for d in _docs(index)
                              if d.get("meta", {}).get("kind") == FORMULA_KIND}
    return index["_formulas"]


def _docs(index: dict):
    docs = index["docs"]
    return docs.values() if isinstance(docs, dict) else docs


def _is_legend(doc: dict) -> bool:
    return doc.get("meta", {}).get("kind") == "ma_sp"


def masp_catalog(index: dict) -> list[tuple[str, str]]:
    """Mã SP legend learned from the knowledge base: ``[(product type, code)]``.

    One entry per code (case-insensitive), in the order the golden legends
    list them; the most frequent label wins for each code.
    """
    order: list[str] = []
    labels: dict[str, dict[str, int]] = {}
    for doc in _docs(index):
        if not _is_legend(doc):
            continue
        code = str(doc["meta"].get("ma_sp") or "").strip().lower()
        if not code:
            continue
        if code not in labels:
            order.append(code)
            labels[code] = {}
        label = doc["text"].strip()
        labels[code][label] = labels[code].get(label, 0) + 1
    return [(max(labels[c], key=labels[c].get), c) for c in order]


def masp_model(index: dict):
    """Mã SP classifier learned from the golden rows (cached on the index)."""
    if "_masp" not in index:
        from .masp import MaSpModel
        index["_masp"] = MaSpModel(_docs(index), masp_catalog(index))
    return index["_masp"]


def amount_recipe(index: dict) -> str | None:
    """The amount (thành tiền) formula most golden rows use, e.g.
    ``[don_gia]*[khoi_luong]``."""
    counts: dict[str, int] = {}
    for doc in _docs(index):
        f = doc.get("meta", {}).get("cong_thuc_tt")
        if f:
            counts[f] = counts.get(f, 0) + 1
    return max(counts, key=counts.get) if counts else None


_SIZES = ("w1", "h1", "w2", "h2", "w3", "h3", "l", "r", "e")


def _richness(doc: dict) -> tuple:
    """How completely a golden row is filled: priced, with a Mã SP column,
    with a code, with a formula, number of area-table sizes."""
    m = doc.get("meta", {})
    return (bool(doc.get("priced")), m.get("ma_sp") is not None, bool(m.get("ma_sp")),
            bool(m.get("cong_thuc")), sum(m.get(k) is not None for k in _SIZES))


def build_exact_map(index: dict) -> dict[str, dict]:
    """Normalised golden text -> the most completely filled golden row.

    Used to pin a product to a known quotation rather than extrapolating from
    a similar-but-different product; the same name often appears in several
    workbooks, some without the area table.
    """
    out: dict[str, dict] = {}
    for doc in _docs(index):
        if doc.get("meta", {}).get("kind"):  # legend / formula documents
            continue
        key = _norm(doc["text"])
        cur = out.get(key)
        if cur is None or _richness(doc) > _richness(cur):
            out[key] = doc
    return out


def exact_match(name: str | None, exact_map: dict[str, dict]) -> dict | None:
    if not name:
        return None
    return exact_map.get(_norm(name))
