# SPDX-License-Identifier: Apache-2.0
"""Bounded generation workers; SQLite and all outward writes stay on main thread."""
from concurrent.futures import ThreadPoolExecutor
import time
from autoreply_metrics import NO_TIMINGS


class GenerationPool:
    def __init__(self, queue, generator, workers=2, timings=NO_TIMINGS):
        self.queue = queue
        self.generator = generator
        self.capacity = workers
        self.timings = timings
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='rag')
        self.running = {}
        self.next_index = 0

    def submit(self, contacts, now, merge=2, max_age=180):
        count = len(contacts)
        if not count: return
        start = self.next_index % count
        for offset in range(count):
            position = (start + offset) % count
            contact = contacts[position]
            peer = contact['username']
            if peer in self.running or len(self.running) >= self.capacity: continue
            task = self.queue.prepare_generation(contact, now, merge, max_age)
            if task:
                future = self.executor.submit(self._generate, task)
                self.running[peer] = (task, future)
                self.next_index = (position + 1) % count

    def _generate(self, task):
        with self.timings.task(task['trace']), self.timings.span('generation'):
            # Generator opens its own read-only index. It never touches queue.db/UI.
            return self.generator.generate(task['contact'], task['history'])

    def collect(self, now=None, max_age=180):
        now = time.time() if now is None else now
        for peer, (task, future) in list(self.running.items()):
            if not future.done(): continue
            del self.running[peer]
            try: result, error = future.result(), None
            except Exception as problem: result, error = None, problem
            self.queue.complete_generation(task, result, error, now, max_age)

    def close(self):
        # Outstanding HTTP requests may finish, but cannot write queue state or send.
        self.executor.shutdown(wait=False, cancel_futures=True)
