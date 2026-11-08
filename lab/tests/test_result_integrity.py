import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines.run_queue import QueuedRunManager
from lab.model import empty_project
from lab.result_json import load_json, load_result
from lab.server import create_app
from lab.report import _load


class ResultIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        setting = patch('lab.engines.manager.RESULTS_DIR', self.temp.name)
        setting.start()
        self.addCleanup(setting.stop)
        bus = EventBus()
        self.manager = QueuedRunManager(bus, DemoDriver(bus), workers=1)
        self.addCleanup(self.manager.close)
        self.result = {'run_id': 'sample', 'status': 'done', 'project': 'history',
                       'engine': 'analytic', 'created': 1, 'flows': {'f': {'series': [{'thr_bps': 123}]}}}

    def test_nonfinite_values_are_rejected_in_nested_series_and_cohorts(self):
        for token in ('NaN', 'Infinity', '-Infinity', '1e999'):
            for text in ('{"flows":{"f":{"series":[{"thr_bps":'+token+'}]}}}', '[{"created":'+token+'}]'):
                with self.subTest(token=token), self.assertRaises(ValueError):
                    load_json(io.StringIO(text))
        self.assertEqual(load_result(io.StringIO('{"run_id":"r","bytes":18446744073709551615}'), 'r')['bytes'], 2**64-1)

    def test_shapes_and_filename_identity_are_checked(self):
        for text in ('[]', 'null', '3', '{"run_id":"other"}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                load_result(io.StringIO(text), 'expected')

    def test_failed_serialization_and_replace_preserve_previous_result(self):
        self.manager._save('history', self.result)
        path = Path(self.temp.name, 'history', 'sample.json')
        before = path.read_bytes()
        for value in (float('nan'), float('inf'), object()):
            bad = copy.deepcopy(self.result)
            bad['flows']['f']['series'][0]['thr_bps'] = value
            with self.assertRaises((ValueError, TypeError)):
                self.manager._save('history', bad)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob('*.tmp')), [])
        with patch('lab.engines.manager.os.replace', side_effect=OSError('replace failure')), self.assertRaises(OSError):
            self.manager._save('history', self.result)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_history_skips_corruption_and_retrieval_refuses_it_then_recovers(self):
        self.manager._save('history', self.result)
        self.assertEqual(len(self.manager.list('history')), 1)
        path = Path(self.temp.name, 'history', 'sample.json')
        for raw in ('NaN', 'Infinity', '1e999'):
            path.write_text(json.dumps(self.result).replace('123', raw))
            self.assertEqual(self.manager.list('history'), [])
            with self.assertRaises(ValueError):
                self.manager.get('history', 'sample')
        path.write_text(json.dumps(self.result))
        self.assertEqual(len(self.manager.list('history')), 1)

    def test_oversized_created_timestamp_is_normalized_without_overflow(self):
        for created in (10**1000, float('nan'), float('inf'), True):
            self.manager.store.save({**self.result, 'created': created})
            self.assertEqual(self.manager.store.get('history', 'sample')['created'], 0)
        path = Path(self.temp.name, 'history', 'sample.json')
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({**self.result, 'created': 10**1000}))
        self.assertEqual(self.manager.list('history')[0]['created'], 0)

    def test_invalid_engine_result_finishes_as_error_without_partial_archive(self):
        project = empty_project('bad_engine')
        project['scenarios']['test'] = {'duration': 1, 'apps': [], 'events': []}
        with patch('lab.engines.analytic.run', return_value={'duration': 1, 'flows': {}, 'metric': float('nan')}):
            rid = self.manager.start(project, 'test', 'analytic')['run_id']
            deadline = time.monotonic() + 5
            # The engine may release active progress before the queue's exception
            # handler commits its error row. Await queue ownership cleanup.
            while self.manager.futures and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(self.manager.futures)
        self.assertEqual(self.manager.get(project['name'], rid)['status'], 'error')
        self.assertFalse(Path(self.temp.name, project['name'], rid+'.json').exists())

    def test_api_history_remains_usable_with_a_corrupt_result(self):
        self.manager._save('history', self.result)
        path = Path(self.temp.name, 'history', 'sample.json')
        path.write_text(json.dumps(self.result).replace('123', 'NaN'))
        with TestClient(create_app('demo')) as client:
            response = client.get('/api/runs/history')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['runs'], [])
            self.assertEqual(client.get('/api/runs/history/sample').status_code, 400)
            path.write_text(json.dumps(self.result))
            self.assertEqual(client.get('/api/runs/history/sample').status_code, 200)

    def test_committed_measurements_survive_index_failure_and_reconcile(self):
        project = empty_project('committed')
        project['scenarios']['test'] = {'duration': 1, 'apps': [], 'events': []}
        with patch.object(self.manager.result_index, 'record', side_effect=OSError('index unavailable')), \
                patch('lab.engines.analytic.run', return_value={'duration': 1, 'flows': {}}), \
                patch.object(self.manager.bus, 'publish') as publish:
            rid = self.manager.start(project, 'test', 'analytic')['run_id']
            deadline = time.monotonic() + 5
            while self.manager.active and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(self.manager.active)
            notices = [c.args[0] for c in publish.call_args_list if c.args[0].get('done')]
            self.assertTrue(notices)
            self.assertEqual(notices[-1]['status'], 'done')
        self.assertEqual(self.manager.get(project['name'], rid)['status'], 'done')
        self.assertEqual(self.manager.list(project['name'])[0]['status'], 'done')

    def test_offline_result_arrays_remain_supported_but_nonfinite_values_fail(self):
        path = Path(self.temp.name, 'cohort.json')
        path.write_text(json.dumps([self.result]))
        self.assertEqual(_load(str(path)), [self.result])
        path.write_text(json.dumps([self.result]).replace('123', '1e999'))
        with self.assertRaises(ValueError):
            _load(str(path))
