import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines.run_queue import QueuedRunManager
from lab.engines.run_store import RunStore, TERMINAL
from lab.model import empty_project


class CancellationRaceTests(unittest.TestCase):
    def test_terminal_worker_commit_cannot_be_overwritten_by_cancel_request(self):
        started, written, release = threading.Event(), threading.Event(), threading.Event()

        def engine(project, scenario, progress, log):
            started.set()
            while not release.wait(.005):
                progress(.25)
            return {'flows': {}}

        with tempfile.TemporaryDirectory() as td, patch('lab.engines.manager.RESULTS_DIR', td), \
                patch('lab.engines.analytic.run', side_effect=engine):
            bus = EventBus()
            manager = QueuedRunManager(bus, DemoDriver(bus), workers=1)
            project = empty_project('race')
            project['scenarios'] = {'test': {'duration': 1, 'apps': [], 'events': []}}
            try:
                rid = manager.start(project, 'test', 'analytic')['run_id']
                self.assertTrue(started.wait(3))
                future = manager.futures[rid]
                cancel, record = future.cancel, manager.result_index.record

                def committed(project_name, result):
                    record(project_name, result)
                    if result['status'] in TERMINAL:
                        written.set()

                def wait_for_commit():
                    result = cancel()
                    self.assertFalse(result)  # The engine has already started.
                    self.assertTrue(written.wait(3), 'worker failed to commit its terminal result')
                    return result

                with patch.object(manager.result_index, 'record', side_effect=committed), \
                        patch.object(future, 'cancel', side_effect=wait_for_commit):
                    response = manager.cancel('race', rid)
                future.result(timeout=3)
                self.assertEqual(response['status'], 'cancelled')
                self.assertEqual(manager.store.get('race', rid)['status'], 'cancelled')
                self.assertEqual(manager.list('race')[0]['status'], 'cancelled')
                self.assertEqual(json.loads(Path(td, 'race', rid+'.json').read_text())['status'], 'cancelled')
                self.assertNotIn(rid, manager.active)
            finally:
                release.set()
                manager.close()


class CancellationTransitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RunStore(Path(self.temp.name, 'runs.sqlite3'))

    def test_all_terminal_states_and_result_signatures_survive(self):
        for state in sorted(TERMINAL):
            original = {'project': 'one', 'run_id': state, 'status': state, 'label': 'latest', 'error': 'evidence'}
            self.store.save(original, signature=(123, 456))
            before = self.store.get('one', state)
            self.assertEqual(self.store.mark_cancelling('one', state), before)
            self.assertEqual(self.store.get('one', state), before)
            self.assertEqual(self.store.signatures('one')[state], (123, 456))

    def test_transition_uses_current_metadata_and_is_project_scoped(self):
        for state in ('queued', 'running', 'cancelling'):
            self.store.save({'project': 'one', 'run_id': state, 'status': state, 'label': 'latest'}, signature=(1, 2))
            self.store.save({'project': 'two', 'run_id': state, 'status': 'running'})
            result = self.store.mark_cancelling('one', state)
            self.assertEqual((result['status'], result['label']), ('cancelling', 'latest'))
            self.assertEqual(self.store.signatures('one')[state], (1, 2))
            self.assertEqual(self.store.get('two', state)['status'], 'running')
        with self.assertRaises(FileNotFoundError): self.store.mark_cancelling('one', 'absent')
