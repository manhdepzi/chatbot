from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.parser_engine import normalize_text


STOPWORDS = {
    "va",
    "hoac",
    "kem",
    "theo",
    "lap",
    "dat",
    "gia",
    "cong",
    "tao",
    "kt",
    "mm",
    "cai",
    "bo",
    "m",
    "m2",
    "gio",
    "ong",
}


def item_retrieval_text(item: Dict[str, Any]) -> str:
    parts = [
        item.get("description"),
        item.get("category"),
        item.get("material"),
        item.get("unit"),
        item.get("quote_material_spec"),
        item.get("quote_note"),
        item.get("remark"),
    ]
    return normalize_text(" ".join(str(part or "") for part in parts))


def _tokens(text: str) -> set[str]:
    raw = re.findall(r"[a-z0-9]+", normalize_text(text))
    expanded: List[str] = []
    for token in raw:
        if token == "lcct":
            expanded.extend(["luoi", "chan", "con", "trung"])
            continue
        if token == "louver":
            token = "lover"
        if len(token) > 1 and token not in STOPWORDS:
            expanded.append(token)
    return set(expanded)


def _dimension_similarity(current: Dict[str, Any], memory: Dict[str, Any]) -> Tuple[float, int]:
    keys = [
        ("width", "width"),
        ("height", "height"),
        ("diameter", "diameter"),
        ("length", "length"),
    ]
    matched = 0
    compared = 0
    for current_key, memory_key in keys:
        current_value = current.get(current_key)
        memory_value = memory.get(memory_key)
        if current_value in (None, "", 0) or memory_value in (None, "", 0):
            continue
        try:
            current_float = float(current_value)
            memory_float = float(memory_value)
        except (TypeError, ValueError):
            continue
        compared += 1
        tolerance = max(2.0, abs(current_float) * 0.01)
        if abs(current_float - memory_float) <= tolerance:
            matched += 1
    if compared == 0:
        return 0.0, 0
    return matched / compared, compared


def quote_memory_similarity(current: Dict[str, Any], memory: Dict[str, Any]) -> float:
    current_text = item_retrieval_text(current)
    memory_text = normalize_text(memory.get("normalized_description") or item_retrieval_text(memory))
    current_description = normalize_text(current.get("description"))
    current_tokens = _tokens(current_text)
    memory_tokens = _tokens(memory_text)
    if current_tokens or memory_tokens:
        lexical = len(current_tokens & memory_tokens) / max(len(current_tokens | memory_tokens), 1)
    else:
        lexical = 0.0

    dim_score, compared_dims = _dimension_similarity(current, memory)
    category_score = 1.0 if normalize_text(current.get("category")) == normalize_text(memory.get("category")) else 0.0
    unit_score = 1.0 if normalize_text(current.get("unit")) == normalize_text(memory.get("unit")) else 0.0

    score = (lexical * 0.50) + (dim_score * 0.30) + (category_score * 0.15) + (unit_score * 0.05)
    if current_description and memory_text and current_description == memory_text:
        score += 0.12
    if compared_dims == 0:
        score -= 0.18
    if category_score == 0 and normalize_text(current.get("category")) not in {"", "unknown"}:
        score -= 0.12
    if unit_score == 0:
        score -= 0.08
    return round(max(0.0, min(1.0, score)), 4)


def best_quote_memory_candidate(
    item: Dict[str, Any],
    memory_rows: Iterable[Dict[str, Any]],
    min_apply_score: float = 0.92,
) -> Optional[Dict[str, Any]]:
    candidates = []
    for memory in memory_rows:
        unit_price = float(memory.get("quote_unit_price") or 0)
        if unit_price <= 0:
            continue
        score = quote_memory_similarity(item, memory)
        if score <= 0:
            continue
        candidates.append((score, memory))

    if not candidates:
        return None

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best_memory = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0.0
    margin = best_score - second_score

    candidate = dict(best_memory)
    candidate["rag_similarity_score"] = best_score
    candidate["rag_second_score"] = second_score
    candidate["rag_margin"] = round(margin, 4)
    candidate["rag_auto_apply"] = best_score >= min_apply_score and margin >= 0.05
    return candidate
