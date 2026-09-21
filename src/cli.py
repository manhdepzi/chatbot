"""CLI entry point."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import config
from . import pipeline
from .rag import index as rag_index
from .watcher import run_watcher


def _setup_logging():
    config.LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_DIR / "rag-report.log", encoding="utf-8"),
        ],
    )


def cmd_run(args):
    _setup_logging()
    if args.file:
        index = rag_index.ensure(force=args.rebuild_index)
        out = pipeline.process_file(args.file, index=index, force=args.force,
                                    top_k=args.top_k)
        print("OUTPUT:", out)
    else:
        out = pipeline.process_all(force=args.force, top_k=args.top_k)
        print(f"Processed {len(out)} file(s):")
        for o in out:
            print(" -", o)


def cmd_build_index(args):
    _setup_logging()
    info = rag_index.build()
    print(f"Index built: {info['count']} documents -> {config.CHROMA_DIR}")


def cmd_watch(args):
    _setup_logging()
    run_watcher()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rag-report",
                                     description="Kaiyo quotation RAG system")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="process data files once")
    p_run.add_argument("--file", type=str, default=None)
    p_run.add_argument("--force", action="store_true")
    p_run.add_argument("--top-k", type=int, default=None)
    p_run.add_argument("--rebuild-index", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_idx = sub.add_parser("build-index", help="rebuild the RAG index")
    p_idx.set_defaults(func=cmd_build_index)

    p_watch = sub.add_parser("watch", help="watch data/ for new files")
    p_watch.set_defaults(func=cmd_watch)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
