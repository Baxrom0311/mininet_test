import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines.run_queue import QueuedRunManager
from lab.server import create_app


class InvalidIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        setting = patch('lab.engines.manager.RESULTS_DIR', self.temp.name)
        setting.start()
        self.addCleanup(setting.stop)
        bus = EventBus()
        self.manager = QueuedRunManager(bus, DemoDriver(bus), workers=1)
        self.addCleanup(lambda: self.manager.close())

    def write(self, rid='bad', project='history', valid=False):
        path = Path(self.temp.name, project, rid+'.json')
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'run_id': rid, 'status': 'done', 'created': 1}) if valid else '{broken')
        return path

    def test_bad_archives_are_not_parsed_on_each_poll_or_restart(self):
        self.write()
        self.assertEqual(self.manager.reindex('history')['invalid'], 1)
        self.manager.close()
        bus = EventBus()
        self.manager = QueuedRunManager(bus, DemoDriver(bus), workers=1)
        with patch('lab.engines.result_index.load_result', side_effect=AssertionError('reread')):
            self.assertEqual(self.manager.list('history'), [])
            page = self.manager.invalid_results('history')
        self.assertEqual(page['total'], 1)
        self.assertTrue(page['files'][0]['error'])

    def test_force_reindex_reparses_unchanged_invalid_files(self):
        self.write()
        self.manager.reindex('history')
        from lab.engines.result_index import load_result
        with patch('lab.engines.result_index.load_result', wraps=load_result) as reader:
            self.assertEqual(self.manager.reindex('history')['invalid'], 1)
            self.assertEqual(reader.call_count, 1)

    def test_repaired_or_removed_files_clear_diagnostics(self):
        path = self.write()
        self.manager.reindex('history')
        self.write(valid=True)
        self.assertEqual(self.manager.list('history')[0]['run_id'], 'bad')
        self.assertEqual(self.manager.invalid_results('history')['total'], 0)
        path.write_text('{again')
        self.manager.reindex('history')
        path.unlink()
        self.assertEqual(self.manager.invalid_results('history')['total'], 0)

    def test_project_isolation_pagination_and_bounded_errors(self):
        for i in range(3): self.write('bad'+str(i))
        self.write('other', project='another')
        self.manager.reindex('another')
        page = self.manager.invalid_results('history', limit=1, offset=1)
        self.assertEqual(page['total'], 3)
        self.assertEqual([r['run_id'] for r in page['files']], ['bad1'])
        self.manager.store.reject_result('history', 'long', (1,2), 'x'*2000)
        self.assertEqual(len(self.manager.store.invalid_page('history')['files'][-1]['error']), 500)
        for limit, offset in ((0,0),(1001,0),(True,0),(1,-1),(1,1000001)):
            with self.assertRaises(ValueError): self.manager.store.invalid_page('history',limit,offset)
            with patch.object(self.manager.result_index, 'reconcile') as scan:
                with self.assertRaises(ValueError): self.manager.invalid_results('history',limit,offset)
                scan.assert_not_called()

    def test_storage_failure_does_not_quarantine_valid_json(self):
        self.write(valid=True)
        with patch.object(self.manager.store, 'save', side_effect=OSError('database unavailable')):
            with self.assertRaises(OSError): self.manager.reindex('history')
        self.assertEqual(self.manager.store.invalid_page('history')['total'], 0)

    def test_transient_read_failure_is_not_cached(self):
        self.write(valid=True)
        with patch('lab.engines.result_index.open', side_effect=PermissionError('temporary')):
            self.assertEqual(self.manager.reindex('history')['invalid'], 1)
        self.assertEqual(self.manager.store.invalid_page('history')['total'], 0)
        self.assertEqual(len(self.manager.list('history')), 1)

    def test_pending_worker_metadata_is_not_removed_by_bad_external_file(self):
        path = self.write()
        self.manager.store.save({'project':'history','run_id':'bad','status':'running'})
        self.manager.reindex('history')
        self.assertEqual(self.manager.store.get('history','bad')['status'], 'running')
        path.unlink()
        self.assertEqual(self.manager.invalid_results('history')['total'], 0)
        self.assertEqual(self.manager.store.get('history','bad')['status'], 'running')

    def test_failed_metadata_save_retains_diagnostic_until_success(self):
        self.write()
        self.manager.reindex('history')
        self.write(valid=True)
        with patch.object(self.manager.store, '_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.manager.reindex('history')
        self.assertEqual(self.manager.store.invalid_page('history')['total'], 1)
        self.assertEqual(self.manager.reindex('history')['updated'], 1)
        self.assertEqual(self.manager.store.invalid_page('history')['total'], 0)

    def test_api_exposes_errors_without_new_jobs_and_repair_recovers(self):
        self.write()
        with TestClient(create_app('demo', token='test-token')) as client:
            self.assertEqual(client.get('/api/results/history/invalid').status_code, 401)
            client.cookies.set('netlab_token', 'test-token')
            response = client.get('/api/results/history/invalid?limit=1')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['total'], 1)
            self.assertEqual(client.get('/api/runs/history').json()['runs'], [])
            self.assertFalse(client.app.state.runs.active)
            for query in ('limit=0', 'limit=1001', 'offset=-1', 'offset=1000001'):
                self.assertEqual(client.get('/api/results/history/invalid?'+query).status_code, 400)
            self.assertEqual(client.get('/api/results/history/invalid?limit=no').status_code, 422)
            self.assertEqual(client.get('/api/results/bad.name/invalid').status_code, 400)
            self.write(valid=True)
            self.assertEqual(client.get('/api/results/history/invalid').json()['total'], 0)
            self.assertEqual(client.get('/api/runs/history/bad').status_code, 200)
            self.write('invalid', valid=True)
            self.assertEqual(client.get('/api/runs/history/invalid').json()['run_id'], 'invalid')
