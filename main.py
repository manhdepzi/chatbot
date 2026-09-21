#!/usr/bin/env python3
"""Entry point for the Kaiyo quotation RAG system.

Usage:
    python main.py run              # process all files in data/
    python main.py run --file data/BTN1205926.xlsx
    python main.py watch            # watch data/ for new files
    python main.py build-index      # (re)build the RAG index
"""
from src.cli import main

if __name__ == "__main__":
    main()
