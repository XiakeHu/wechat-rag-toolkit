# SPDX-License-Identifier: Apache-2.0
"""Local duration records. Never records messages, names, URLs or credentials."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
import threading
import time

TRACE = ContextVar('timing_trace', default='')


class Timings:
    def __init__(self, path=None, limit_bytes=8 * 1024 * 1024):
        self.path = Path(path) if path else None
        self.limit_bytes = limit_bytes
        self.lock = threading.Lock()

    def record(self, stage, seconds, trace='', outcome='ok'):
        if self.path is None: return
        entry = dict(time=time.time(), stage=stage, elapsed_ms=round(max(0, seconds)*1000, 3),
                     trace=trace or TRACE.get(), outcome=outcome)
        # Diagnostics must never interrupt a dispatch or change its receipt.
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size >= self.limit_bytes:
                    self.path.replace(self.path.with_suffix('.previous.jsonl'))
                with self.path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError:
            pass

    @contextmanager
    def task(self, trace):
        token = TRACE.set(trace)
        try: yield
        finally: TRACE.reset(token)

    @contextmanager
    def span(self, stage, trace=''):
        start = time.perf_counter()
        outcome = 'ok'
        try:
            yield
        except BaseException:
            outcome = 'error'
            raise
        finally:
            self.record(stage, time.perf_counter()-start, trace, outcome)


NO_TIMINGS = Timings()
