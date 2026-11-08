import contextlib
import copy
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from lab.report import _num, compare_report, csv_bundle, main, run_report, study_report
from lab.reproducibility import capture, verify
from lab.server import create_app


def result():
    return {'run_id': 'sample', 'project': 'history', 'engine': 'analytic', 'status': 'done',
            'created': 1, 'duration': 2, 'scenario': {'duration': 2},
            'flows': {'f': {'type': 'udp', 'summary': {'avg_thr_bps': 100},
                             'series': [{'t': 1, 'thr_bps': 100}, {'t': 2, 'thr_bps': 100}]}},
            'links': {'l1': [{'t': 1, 'util': .2}]}, 'nodes': {'r1': [{'t': 1, 'routes': 2}]}}


class ReportExportTests(unittest.TestCase):
    def test_bad_containers_are_path_specific_errors_in_all_result_renderers(self):
        cases = [('links', {'l1': [None]}, 'links.l1[0]'), ('nodes', [], 'nodes'),
                 ('apps', {'a': []}, 'apps.a'), ('topology', [], 'topology'),
                 ('availability', {'flows': {'f': []}}, 'availability.flows.f'),
                 ('availability', {'theoretical': []}, 'availability.theoretical'),
                 ('route_events', [{'added': [1]}], 'route_events[0].added'),
                 ('convergence', [None], 'convergence[0]'), ('notes', 'bad', 'notes'),
                 ('events', {}, 'events'), ('flows', {'f': {'series': [1]}}, 'flows.f.series[0]')]
        for field, value, message in cases:
            run = result()
            run[field] = value
            for render in (run_report, csv_bundle, lambda r: compare_report([r]), lambda r: study_report([r])):
                with self.subTest(field=field, renderer=render), self.assertRaises(ValueError) as raised:
                    render(run)
                self.assertIn(message, str(raised.exception))

    def test_null_and_absent_optional_sections_remain_supported_without_mutation(self):
        run = result()
        for key in ('topology', 'scenario', 'links', 'nodes', 'apps', 'availability', 'convergence', 'route_events', 'events', 'notes'):
            run[key] = None
        run['flows']['f'].update(summary=None, series=None)
        before = copy.deepcopy(run)
        self.assertIn('Natija', run_report(run))
        with zipfile.ZipFile(io.BytesIO(csv_bundle(run))) as archive:
            self.assertEqual(json.loads(archive.read('results.json')), before)
        self.assertEqual(run, before)

    def test_empty_cohorts_create_valid_archives_and_empty_html_reports(self):
        with zipfile.ZipFile(io.BytesIO(csv_bundle([]))) as archive:
            self.assertEqual(json.loads(archive.read('results.json')), [])
            self.assertEqual(len(list(csv.reader(io.StringIO(archive.read('flows.csv').decode())))), 1)
            self.assertIn('flow_summary.csv', archive.namelist())
        self.assertIn('natija yo', compare_report([]))
        self.assertIn('tadqiqot natijasi', study_report([]))
        for runs in (None, [None], [False], 'result'):
            with self.assertRaises(ValueError): csv_bundle(runs)

    def test_large_exact_counters_and_unrepresentable_timestamp_render_safely(self):
        run = result()
        counter = 2**64-1
        run['nodes']['r1'][0]['routes'] = counter
        run['created'] = 10**400
        html = run_report(run)
        self.assertIn(_num(counter), html)
        self.assertEqual(_num(10**400).replace(' ', ''), str(10**400))
        with zipfile.ZipFile(io.BytesIO(csv_bundle(run))) as archive:
            row = next(csv.DictReader(io.StringIO(archive.read('nodes.csv').decode())))
            self.assertEqual(row['routes'], str(counter))
            self.assertEqual(json.loads(archive.read('results.json'))['nodes']['r1'][0]['routes'], counter)

    def test_raw_export_rejects_non_json_or_nonfinite_values(self):
        for value in (float('nan'), float('inf'), object()):
            run = result()
            run['opaque_extension'] = value
            with self.assertRaises(ValueError): csv_bundle(run)
        for value in ('100', True, 10**400):
            run = result()
            run['flows']['f']['summary']['avg_thr_bps'] = value
            with self.assertRaises(ValueError): run_report(run)

    def test_replay_export_requires_a_usable_verified_request(self):
        run = result()
        scenario = run['scenario']
        project = {'name': 'history', 'scenarios': {'test': scenario}}
        record = capture(project, 'test', scenario, scenario, 'analytic')
        run['reproducibility'] = record
        with zipfile.ZipFile(io.BytesIO(csv_bundle(run))) as archive:
            self.assertEqual(json.loads(archive.read('run-request.json'))['scenario'], 'test')
        for change in ('missing_name', 'scenario_shape', 'engine', 'manifest_list'):
            bad = copy.deepcopy(run)
            if change == 'missing_name': bad['reproducibility'].pop('scenario_name')
            elif change == 'scenario_shape': bad['scenario'] = {'duration': 99}
            elif change == 'engine': bad['engine'] = 'ns3'
            else: bad['reproducibility'] = ['bad']
            if change == 'missing_name': self.assertTrue(verify(bad['reproducibility']))
            with self.subTest(change=change), zipfile.ZipFile(io.BytesIO(csv_bundle(bad))) as archive:
                self.assertNotIn('run-request.json', archive.namelist())
                self.assertIn('replay-unavailable.txt', archive.namelist())
                self.assertEqual(json.loads(archive.read('results.json')), bad)

    def test_extreme_chart_numbers_terminate_and_do_not_emit_nonfinite_coordinates(self):
        code = '''
import re
from lab.report import svg_lines, svg_heatmap
for points in ([(0,5e-324),(1,5e-324)],[(1e308,1),(1.0000000000000002e308,2)],
               [(0,-1e308),(1,1e308)],[(0,None),(1,float('nan')),(2,1)],[(0,1e308),(1,1e308)]):
    svg = svg_lines([('test',points)],'test')
    assert not re.search(r'\\b(?:nan|inf)\\b', svg, re.I), svg
heatmap = svg_heatmap(['a','b'],['x'],[[-1e308,1e308]],'test')
assert '#cde2fb' in heatmap and '#0d366b' in heatmap
assert not re.search(r'\\b(?:nan|inf)\\b', heatmap, re.I)
'''
        proc = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_cli_render_and_replace_failures_preserve_existing_output(self):
        with tempfile.TemporaryDirectory() as td:
            src, out = Path(td, 'input.json'), Path(td, 'output.zip')
            out.write_bytes(b'previous report')
            src.write_text(json.dumps({'run_id': 'sample', 'links': {'l1': [None]}}))
            with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as raised:
                main([str(src), '--zip', '-o', str(out)])
            self.assertEqual(raised.exception.code, 2)
            self.assertIn('links.l1[0]', errors.getvalue())
            self.assertEqual(out.read_bytes(), b'previous report')
            src.write_text(json.dumps(result()))
            with patch('lab.report.os.replace', side_effect=OSError('replace failed')), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main([str(src), '--zip', '-o', str(out)])
            self.assertEqual(out.read_bytes(), b'previous report')
            self.assertEqual(set(Path(td).iterdir()), {src, out})
            main([str(src), '--zip', '-o', str(out)])
            self.assertTrue(zipfile.is_zipfile(out))
            main([str(src), '-o', str(out)])
            self.assertIn('<!doctype html>', out.read_text())

    def test_cli_directory_identity_mismatch_is_a_controlled_error(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td, 'inputs')
            src.mkdir()
            (src/'wrong.json').write_text(json.dumps(result()))
            out = Path(td, 'out.html')
            with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit):
                main([str(src), '-o', str(out)])
            self.assertIn('run_id must match', errors.getvalue())
            self.assertFalse(out.exists())


class ReportExportAPITests(unittest.TestCase):
    def test_malformed_archives_return_400_across_reports_and_repair_recovers(self):
        with tempfile.TemporaryDirectory() as td, patch('lab.engines.manager.RESULTS_DIR', td), \
                TestClient(create_app('demo')) as client:
            manager = client.app.state.runs
            run = result()
            run.update(study_id='study', study_index=0)
            bad = copy.deepcopy(run)
            bad['links']['l1'] = [None]
            manager._save('history', bad)
            for path in ('/api/runs/history/sample/report.html', '/api/runs/history/sample/export.zip',
                         '/api/studies/history/study/report.html'):
                response = client.get(path)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn('links.l1[0]', response.json()['error'])
            response = client.post('/api/reports/compare', json={'project':'history','run_ids':['sample']})
            self.assertEqual(response.status_code, 400, response.text)
            manager._save('history', run)
            self.assertEqual(client.get('/api/runs/history/sample/report.html').status_code, 200)
            self.assertEqual(client.get('/api/runs/history/sample/export.zip').status_code, 200)
            self.assertEqual(client.get('/api/studies/history/study/report.html').status_code, 200)
