"""Watch the data/ directory and process new files as they appear."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from . import config
from . import pipeline
from .rag import index as rag_index

log = logging.getLogger("watcher")

DEBOUNCE_SECONDS = 1.5


class _Handler(FileSystemEventHandler):
    def __init__(self):
        self._pending: dict[str, float] = {}
        self._index = None

    def _index_loaded(self):
        if self._index is None:
            self._index = rag_index.ensure()
        return self._index

    def _schedule(self, path: str):
        p = Path(path)
        if p.suffix.lower() not in config.INPUT_EXTS:
            return
        if p.name.startswith(config.IGNORE_PREFIXES):
            return
        self._pending[path] = time.time()

    def on_created(self, event):
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._schedule(event.dest_path)

    def tick(self):
        now = time.time()
        ready = [p for p, t in self._pending.items() if now - t >= DEBOUNCE_SECONDS]
        for p in ready:
            self._pending.pop(p, None)
            try:
                out = pipeline.process_file(p, index=self._index_loaded())
                if out:
                    log.info("watcher generated %s", out)
            except Exception as e:  # noqa: BLE001
                log.exception("watcher failed on %s: %s", p, e)


def run_watcher():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = _Handler()
    observer = Observer()
    observer.schedule(handler, str(config.DATA_DIR), recursive=False)
    observer.start()
    log.info("watching %s for new files...", config.DATA_DIR)
    try:
        while True:
            time.sleep(0.5)
            handler.tick()
    except KeyboardInterrupt:
        log.info("stopping watcher")
    finally:
        observer.stop()
        observer.join()
