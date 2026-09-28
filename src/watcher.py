"""Watch the data/ directory and generate a report for every new/changed file."""
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

# a file is processed once it has not changed for this long (copy finished)
DEBOUNCE_SECONDS = 2.0


class _Handler(FileSystemEventHandler):
    def __init__(self):
        self._pending: dict[str, tuple[float, int]] = {}
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
        self._pending[path] = (time.time(), _size(p))

    def on_created(self, event):
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._schedule(event.dest_path)

    def tick(self):
        now = time.time()
        for path, (t, size) in list(self._pending.items()):
            if now - t < DEBOUNCE_SECONDS:
                continue
            p = Path(path)
            if not p.exists():
                self._pending.pop(path, None)
                continue
            # still being written: wait another round
            cur = _size(p)
            if cur != size:
                self._pending[path] = (now, cur)
                continue
            self._pending.pop(path, None)
            try:
                out = pipeline.process_file(p, index=self._index_loaded())
                if out:
                    log.info("watcher generated %s", out)
            except Exception as e:  # noqa: BLE001
                log.exception("watcher failed on %s: %s", p, e)


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return -1


def run_watcher():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = _Handler()
    observer = Observer()
    observer.schedule(handler, str(config.DATA_DIR), recursive=False)
    observer.start()
    log.info("watching %s for new files...", config.DATA_DIR)

    # files dropped while the watcher was stopped (no report or report older)
    for p in pipeline.input_files():
        handler._schedule(str(p))

    try:
        while True:
            time.sleep(0.5)
            handler.tick()
    except KeyboardInterrupt:
        log.info("stopping watcher")
    finally:
        observer.stop()
        observer.join()
