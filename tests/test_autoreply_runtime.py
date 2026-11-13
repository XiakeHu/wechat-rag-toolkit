# SPDX-License-Identifier: Apache-2.0
"""Simulated concurrency, routing and timing; no WeChat or remote model calls."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from autoreply_core import Queue
from autoreply_metrics import Timings
from autoreply_runtime import GenerationPool
from wechat_autoreply import make_sender, load


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        index = self.root/'index.sqlite'; index.touch()
        self.contacts = [dict(username=p, enabled=True, auto_send=True,index=str(index)) for p in ('one','two','three')]
        self.q = Queue(self.root/'state.sqlite'); self.pool = None
        self.release = threading.Event()
        for c in self.contacts: self.q.ingest(c['username'],[dict(id='first',role='partner',text='你好',ts=100)],100)

    def tearDown(self):
        self.release.set()
        if self.pool:
            self.pool.executor.shutdown(wait=True,cancel_futures=True)
        self.q.db.close(); self.temp.cleanup()

    def status(self, peer):
        return self.q.db.execute('SELECT status FROM jobs WHERE peer=?',(peer,)).fetchone()[0]

    def test_slow_contact_does_not_block_others_or_ingest(self):
        entered = threading.Event(); release = self.release
        class Model:
            def generate(self,contact,history):
                if contact['username']=='one': entered.set(); release.wait(3)
                return ['候选'],False
        self.pool = GenerationPool(self.q,Model(),workers=2)
        self.pool.submit(self.contacts,103)
        self.assertTrue(entered.wait(1)); self.assertEqual(len(self.pool.running),2)
        # Main-thread DB remains available while the first HTTP request is blocked.
        self.q.ingest('one',[dict(id='human',role='self',text='本人回复',ts=104)],104)
        self.pool.running['two'][1].result(timeout=1)
        self.pool.collect(105)
        self.assertEqual(self.status('two'),'ready');self.assertEqual(self.status('one'),'superseded')
        self.release.set(); self.pool.running['one'][1].result(timeout=1); self.pool.collect(106)
        self.assertEqual(self.status('one'),'superseded')

    def test_single_task_per_peer_and_bounded_capacity(self):
        release = self.release
        class Model:
            def generate(self,contact,history): release.wait(3);return ['候选'],False
        self.pool = GenerationPool(self.q,Model(),workers=1)
        self.pool.submit(self.contacts,103);self.pool.submit(self.contacts,104)
        self.assertEqual(list(self.pool.running),['one'])

    def test_stale_success_and_failure_do_not_replace_new_input(self):
        for error in (None,RuntimeError('private contents')):
            task = self.q.prepare_generation(self.contacts[0],103,merge=2)
            self.q.ingest('one',[dict(id='next'+str(bool(error)),role='partner',text='新消息',ts=104)],104)
            self.assertFalse(self.q.complete_generation(task,(['过期'],False),error,105))
            self.assertEqual(self.status('one'),'pending')
            # The next iteration snapshots the new version after merging.
            if not error:
                self.q.db.execute("UPDATE jobs SET updated=100 WHERE peer='one'");self.q.db.commit()

    def test_expired_result_is_not_ready(self):
        task = self.q.prepare_generation(self.contacts[0],103,merge=2)
        self.q.complete_generation(task,(['候选'],False),None,300,max_age=180)
        self.assertEqual(self.status('one'),'superseded')

    def test_close_with_inflight_request_cannot_update_queue(self):
        release = self.release; done = threading.Event()
        class Model:
            def generate(self,contact,history):
                release.wait(3);done.set();return ['候选'],False
        self.pool = GenerationPool(self.q,Model(),workers=1)
        self.pool.submit(self.contacts,103);self.pool.close();self.release.set()
        self.assertTrue(done.wait(1));self.assertEqual(self.status('one'),'pending')

    def test_busy_contact_does_not_starve_waiting_contact(self):
        class Model:
            def generate(self,contact,history):return ['候选'],False
        self.pool = GenerationPool(self.q,Model(),workers=1)
        self.pool.submit(self.contacts,103)
        self.q.ingest('one',[dict(id='new',role='partner',text='继续发',ts=104)],104)
        self.pool.running['one'][1].result(timeout=1);self.pool.collect(105)
        self.pool.submit(self.contacts,107)
        self.assertEqual(list(self.pool.running),['two'])


class DiagnosticTests(unittest.TestCase):
    def test_sender_factory_has_one_implementation(self):
        from native_uia_sender import NativeUIASender
        self.assertIsInstance(make_sender({}),NativeUIASender)

    def test_timings_omit_exception_contents_and_correlate_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'timings.jsonl';metrics=Timings(path)
            with self.assertRaises(ValueError):
                with metrics.task('random-task-id'),metrics.span('embedding'):
                    raise ValueError('private-token-and-message')
            text=path.read_text(encoding='utf-8');row=json.loads(text)
            self.assertNotIn('private-token',text);self.assertEqual(row['trace'],'random-task-id')
            self.assertEqual(row['outcome'],'error');self.assertGreaterEqual(row['elapsed_ms'],0)

    def test_rotation_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'timings.jsonl';metrics=Timings(path,limit_bytes=1)
            for _ in range(4):metrics.record('poll',.01)
            self.assertEqual(len(list(Path(directory).iterdir())),2)

    def test_invalid_timing_and_worker_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json'
            for key,value in [('poll_seconds',0),('poll_seconds',True),('merge_seconds',float('nan')),('generation_workers',0),('generation_workers',True)]:
                path.write_text(json.dumps(dict(enabled=False,contacts=[],**{key:value})),encoding='utf-8')
                with self.assertRaises(ValueError):load(path)


if __name__=='__main__': unittest.main()
