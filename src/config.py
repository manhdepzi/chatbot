"""Central configuration: paths, env, constants."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

import logging

# silence noisy httpx/openai/chroma debug logs
for _name in ("httpx", "httpx2", "openai", "httpcore", "chromadb", "opentelemetry"):
    logging.getLogger(_name).setLevel(logging.WARNING)

# Project root = parent of src/
ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")

# --- Paths -------------------------------------------------------------
DATA_DIR = ROOT / "data"
TEMPLATE_DIR = ROOT / "report-template"
GOLDEN_DIR = ROOT / "golden-data"
REPORT_DIR = ROOT / "reports"          # output dir (was `report/` in the brief)
INDEX_DIR = ROOT / "rag_index"
LOG_DIR = ROOT / "logs"

TEMPLATE_FILE = TEMPLATE_DIR / "kaiyo_quotation_template.xlsx"
INDEX_FILE = INDEX_DIR / "index.json"
STATE_FILE = INDEX_DIR / "state.json"

# --- ChromaDB ------------------------------------------------------------
CHROMA_DIR = INDEX_DIR / "chroma"       # persistent vector store on disk
CHROMA_COLLECTION = "golden_quotes"

# --- OpenAI -------------------------------------------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

# --- RAG -----------------------------------------------------------------
RAG_TOP_K = 3

# --- Supported extensions -------------------------------------------------
INPUT_EXTS = {".xls", ".xlsx"}
IGNORE_PREFIXES = ("~$", ".")
