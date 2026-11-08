import contextlib
import copy
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from lab.benchmarks.export_memory import benchmark, fingerprints, fixture
from lab.campaign_report import write_campaign_bundle
from lab.experiment_comparison import compare
from lab.experiment_comparison_report import write_comparison_bundle
from lab.experiments import summarize
from lab.report import main, write_experiment_bundle
from lab.report_export import csv_bundle, write_csv, write_csv_bundle, write_json
from lab.server import create_app
from lab.tests.test_experiment_comparison import cohort
from lab.tests.test_report_export_integrity import result
from lab.topogen import generate


class NonSeekable(io.BytesIO):
    def seekable(self):
        return False

    def seek(self, *args):
        raise io.UnsupportedOperation('nonseekable output')

    def tell(self):
        raise io.UnsupportedOperation('nonseekable output')


class StreamExportTests(unittest.TestCase):
    def test_file_and_nonseekable_targets_preserve_all_entry_contents(self):
        run = result()
        run['flows']['f']['dst'] = ['h2', 'h3']
        run['apps'] = {'web': {'type': 'http', 'url': 'a,b\nO‘zbekcha'}}
        run['availability'] = {'flows': {'f': {'availability_pct': 99, 'downtime_s': 1, 'outages': [{}]}}}
        run['convergence'] = [{'t': 1, 'devices': ['r1', 'r2'], 'converged_s': .5}]
        run['route_events'] = [{'t': 1, 'dev': 'r1', 'added': ['::/0'], 'removed': []}]
        run['events'] = [{'t': 1, 'action': 'link_down', 'link': 'l1'}]
        before = copy.deepcopy(run)
        expected = fingerprints(csv_bundle(run))
        for target in (io.BytesIO(), NonSeekable()):
            with self.subTest(target=type(target).__name__):
                write_csv_bundle(run, target)
                self.assertFalse(target.closed)
                self.assertEqual(fingerprints(target.getvalue()), expected)
        self.assertEqual(run, before)
        self.assertEqual(set(expected), {'flows.csv', 'flow_summary.csv', 'links.csv', 'nodes.csv', 'apps.csv',
                                        'availability.csv', 'convergence.csv', 'route_events.csv', 'events.csv', 'results.json'})

    def test_csv_rows_are_consumed_once_with_exact_unicode_and_integer_values(self):
        consumed = []
        def rows():
            for i in range(3):
                consumed.append(i)
                yield ['comma, quote"\nO‘zbekcha', 2**64-1]
        target = io.BytesIO()
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            write_csv(archive, 'sample.csv', rows(), ['text', 'counter'])
            write_json(archive, 'following.json', {'open': True})
        self.assertEqual(consumed, [0, 1, 2])
        with zipfile.ZipFile(io.BytesIO(target.getvalue())) as archive:
            values = list(csv.DictReader(io.StringIO(archive.read('sample.csv').decode())))
            self.assertEqual(values, [{'text':'comma, quote"\nO‘zbekcha', 'counter':str(2**64-1)}]*3)
            self.assertEqual(json.loads(archive.read('following.json')), {'open': True})

    def test_target_errors_are_not_reported_as_invalid_json(self):
        class BrokenTarget(io.BytesIO):
            def write(self, value):
                raise error
        for error in (OSError('disk full'), ValueError('closed target')):
            target = BrokenTarget()
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)) as raised:
                with zipfile.ZipFile(target, 'w') as archive:
                    write_json(archive, 'results.json', {'valid': True})
            self.assertEqual(str(raised.exception), str(error))
            self.assertFalse(target.closed)

    def test_late_serialization_failure_keeps_atomic_cli_destination(self):
        run = result()
        run['extension'] = object()
        with tempfile.TemporaryDirectory() as td:
            output = Path(td, 'report.zip')
            output.write_bytes(b'previous report')
            with patch('lab.report._load', return_value=run), contextlib.redirect_stderr(io.StringIO()) as errors, \
                    self.assertRaises(SystemExit) as raised:
                main(['ignored.json', '--zip', '-o', str(output)])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn('finite JSON', errors.getvalue())
            self.assertEqual(output.read_bytes(), b'previous report')
            self.assertEqual(list(Path(td).iterdir()), [output])

    def test_file_export_additional_allocations_do_not_scale_with_all_rows(self):
        code = '''
import json
from lab.benchmarks.export_memory import benchmark
r = benchmark(flows=1, points=20000, mode='file')
print(json.dumps({'peak': r['traced_peak_bytes'], 'rows': r['samples']}))
'''
        measured = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
        self.assertEqual(measured.returncode, 0, measured.stderr)
        data = json.loads(measured.stdout)
        self.assertEqual(data['rows'], 20000)
        self.assertLess(data['peak'], 4*1024*1024)

    def test_experiment_and_comparison_writers_support_nonseekable_targets(self):
        left, right = cohort('left', [10, 20]), cohort('right', [11, 22])
        summary, comparison = summarize(left), compare(left, right)
        target = NonSeekable()
        write_experiment_bundle(left, summary, target)
        self.assertFalse(target.closed)
        with zipfile.ZipFile(io.BytesIO(target.getvalue())) as archive:
            self.assertEqual(json.loads(archive.read('experiment.json')), summary)
            self.assertEqual(len(json.loads(archive.read('results.json'))), 2)
        target = NonSeekable()
        write_comparison_bundle(left, right, comparison, target)
        self.assertFalse(target.closed)
        with zipfile.ZipFile(io.BytesIO(target.getvalue())) as archive:
            self.assertEqual(json.loads(archive.read('comparison.json')), comparison)
            self.assertEqual(len(json.loads(archive.read('results.json'))), 4)
            mapping = list(csv.DictReader(io.StringIO(archive.read('run_map.csv').decode())))
            for item in mapping:
                self.assertEqual(item['replay_inputs_available'], 'True')
                self.assertIn(item['input_directory']+'run-request.json', archive.namelist())

    def test_benchmark_validates_bounds_and_compares_uncompressed_content(self):
        for flows, points in ((True,1),(1,0),(1001,1),(1000,100000)):
            with self.assertRaises(ValueError): fixture(flows, points)
        with self.assertRaises(ValueError): benchmark(mode='bad')
        a, b = benchmark(1, 2, 'bytes'), benchmark(1, 2, 'file')
        self.assertEqual(a['entries'], b['entries'])
        self.assertGreater(a['traced_peak_bytes'], 0)

    def test_nested_campaign_writer_and_http_download_remain_readable(self):
        with tempfile.TemporaryDirectory() as td, patch('lab.engines.manager.RESULTS_DIR', td), \
                TestClient(create_app('demo')) as client:
            project = generate('ring', routers=3, hosts=1, flows=0)
            project['scenarios'] = {'test': {'duration': 1, 'apps': [], 'events': []}}
            response = client.post('/api/campaigns', json={'project': project, 'scenario': 'test', 'repetitions': 2,
                'param': {'kind': 'link', 'link': next(iter(project['links'])), 'field': 'bw'}, 'values': [20]})
            self.assertEqual(response.status_code, 200, response.text)
            plan = response.json()
            manager = client.app.state.runs
            deadline = time.monotonic()+10
            while manager.futures:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            saved, report, cohorts = manager.campaigns.analysis(project['name'], plan['campaign_id'])
            target = NonSeekable()
            write_campaign_bundle(saved, report, cohorts, target)
            self.assertFalse(target.closed)
            with zipfile.ZipFile(io.BytesIO(target.getvalue())) as archive:
                with zipfile.ZipFile(io.BytesIO(archive.read('experiments/baseline.zip'))) as nested:
                    self.assertTrue(json.loads(nested.read('experiment.json'))['complete'])
            response = client.get(f"/api/campaigns/{project['name']}/{plan['campaign_id']}/export.zip")
            self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
            self.assertEqual(int(response.headers['content-length']), len(response.content))
            self.assertTrue(zipfile.is_zipfile(io.BytesIO(response.content)))
