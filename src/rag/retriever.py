"""Retrieve top-k golden examples for a product name."""
from __future__ import annotations

from . import index as idx
from .. import config
from ..llm import client as llm


def _norm(s: str) -> str:
    return " ".join(s.split()).lower()


def retrieve(name: str, index: dict, top_k: int | None = None) -> list[dict]:
    top_k = top_k or config.RAG_TOP_K
    q = llm.embed([name])[0]
    return idx.search(index, q, top_k, priced_only=True)


def exact_match(name: str, index: dict) -> dict | None:
    """Return the best golden document whose text exactly equals ``name``.

    Prefers a priced document over an unpriced one. Used to pin pricing to a
    known quotation rather than extrapolating from a similar-but-different
    product.
    """
    target = _norm(name)
    match: dict | None = None
    docs = index["docs"]
    docs_iter = docs.values() if isinstance(docs, dict) else docs
    for doc in docs_iter:
        if _norm(doc["text"]) != target:
            continue
        if match is None:
            match = doc
        elif doc.get("priced") and not match.get("priced"):
            match = doc
    return match
