"""Vector index backed by ChromaDB (persistent, on-disk).

Public API mirrors the old JSON index so the rest of the pipeline is
unchanged:

  * ``build()``  -> (re)create the collection from golden-data
  * ``load()``   -> an in-memory index handle (``{docs, collection}``) or None
  * ``ensure()`` -> load, or build if missing/forced
  * ``search()`` -> top-k documents with cosine-similarity scores

Each golden row is stored as one Chroma document. Its id is a stable hash of
(source + text), so rebuilds are idempotent; ``priced`` and the full parsed
metadata are persisted as document metadata and returned on search.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import chromadb

from .. import config
from .golden_parser import parse_golden_dir
from ..llm import client as llm

_BATCH = 64


def _doc_id(text: str, source: str, seq: int) -> str:
    h = hashlib.sha1(f"{source}\x00{text}".encode("utf-8")).hexdigest()[:20]
    return f"{h}-{seq}"


def _client() -> chromadb.ClientAPI:
    config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(config.CHROMA_DIR))


def _collection(client: chromadb.ClientAPI):
    return client.get_or_create_collection(
        name=config.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def _reset_collection(client: chromadb.ClientAPI):
    try:
        client.delete_collection(config.CHROMA_COLLECTION)
    except Exception:  # noqa: BLE001
        pass
    return client.get_or_create_collection(
        name=config.CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def build(golden_dir: str | Path | None = None) -> dict[str, Any]:
    """(Re)build the Chroma collection from golden-data and return {count}."""
    docs = parse_golden_dir(golden_dir or config.GOLDEN_DIR)
    if not docs:
        raise RuntimeError("no golden documents found")

    client = _client()
    col = _reset_collection(client)

    for i in range(0, len(docs), _BATCH):
        chunk = docs[i:i + _BATCH]
        vectors = llm.embed([d["text"] for d in chunk])
        # global sequence index keeps ids stable & aligned with docs_id_map
        ids = [_doc_id(chunk[k]["text"], chunk[k]["source"], i + k)
               for k in range(len(chunk))]
        metadatas = [_flat_meta(d) for d in chunk]
        col.add(
            ids=ids,
            documents=[d["text"] for d in chunk],
            embeddings=vectors,
            metadatas=metadatas,
        )

    # persist the raw docs alongside (id -> doc) so exact_match / search can
    # recover full parsed metadata (incl. lists like dimensions) losslessly.
    _save_docs(docs_id_map(docs))
    return {"count": len(docs)}


def docs_id_map(docs: list[dict]) -> dict[str, dict]:
    return {_doc_id(d["text"], d["source"], i): d for i, d in enumerate(docs)}


def _flat_meta(doc: dict) -> dict[str, Any]:
    meta = doc.get("meta", {})
    flat: dict[str, Any] = {
        "source": doc.get("source", ""),
        "priced": bool(doc.get("priced")),
    }
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, (int, float, str, bool)):
            flat[k] = v
        else:
            flat[k] = json.dumps(v, ensure_ascii=False, default=str)
    return flat


def _save_docs(docs_by_id: dict[str, dict]) -> None:
    config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
    config.INDEX_FILE.write_text(
        json.dumps({"docs": docs_by_id}, ensure_ascii=False))


def _load_docs() -> dict[str, dict]:
    if not config.INDEX_FILE.exists():
        return {}
    try:
        return json.loads(config.INDEX_FILE.read_text()).get("docs", {})
    except (json.JSONDecodeError, AttributeError):
        return {}


def load() -> dict[str, Any] | None:
    """Return an in-memory index handle, or None if the store is empty."""
    client = _client()
    try:
        col = client.get_collection(config.CHROMA_COLLECTION)
    except Exception:  # noqa: BLE001
        return None
    if col.count() == 0:
        return None
    return {"collection": col, "docs": _load_docs()}


def ensure(golden_dir: str | Path | None = None, force: bool = False) -> dict[str, Any]:
    """Load the index, building it if missing or if forced."""
    if not force:
        idx = load()
        if idx and idx.get("docs"):
            return idx
    return build(golden_dir)


def search(index: dict[str, Any], query_vec: list[float], top_k: int,
           priced_only: bool = False) -> list[dict]:
    """Return top-k documents with similarity scores.

    ``priced_only`` restricts results to documents carrying a unit price /
    price factor, which is what the pricing step needs.
    """
    col = index["collection"]
    docs_by_id = index.get("docs", {})

    # query more than needed when filtering, so the final list still has top_k
    n_fetch = top_k * 4 if priced_only else top_k
    res = col.query(
        query_embeddings=[query_vec],
        n_results=min(n_fetch, max(col.count(), 1)),
    )

    out: list[dict] = []
    ids = (res.get("ids") or [[]])[0]
    distances = (res.get("distances") or [[]])[0]
    metadatas = (res.get("metadatas") or [[]])[0]

    for i, doc_id in enumerate(ids):
        meta = _unflat_meta(metadatas[i] if i < len(metadatas) else {})
        full = docs_by_id.get(doc_id)
        if full is not None:
            # full parsed metadata (golden parser output) is authoritative
            doc = dict(full)
        else:
            doc = {"text": (res.get("documents") or [[]])[0][i] if res.get("documents") else "",
                   "meta": meta,
                   "source": meta.get("source", ""),
                   "priced": meta.get("priced", False)}
        if priced_only and not doc.get("priced"):
            continue
        score = 1.0 - (distances[i] if i < len(distances) else 1.0)
        d = dict(doc)
        d["score"] = round(score, 4)
        out.append(d)
        if len(out) >= top_k:
            break

    return out


def _unflat_meta(flat: dict[str, Any] | None) -> dict[str, Any]:
    if not flat:
        return {}
    meta: dict[str, Any] = {}
    for k, v in flat.items():
        if k in ("source", "priced"):
            continue
        if isinstance(v, str) and (v.startswith("[") or v.startswith("{")):
            try:
                meta[k] = json.loads(v)
            except json.JSONDecodeError:
                meta[k] = v
        else:
            meta[k] = v
    meta["source"] = flat.get("source", "")
    meta["priced"] = bool(flat.get("priced"))
    return meta
