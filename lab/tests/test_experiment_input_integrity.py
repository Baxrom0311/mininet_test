import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from lab.analysis_windows import analyze
from lab.experiment_comparison import compare
from lab.experiments import interval, summarize
from lab.reproducibility import digest, verify
from lab.server import create_app
from lab.tests.test_experiment_comparison import cohort
from lab.topogen import generate


class ExperimentInputTests(unittest.TestCase):
    def setUp(self):
        self.runs = cohort('experiment', [10, 20])

    def test_metadata_shapes_fail_before_grouping_or_statistics(self):
        for field, value in (('replication_count', []), ('replication_count', True),
                             ('replication_index', {}), ('replication_index', None),
                             ('experiment_id', []), ('run_id', {}), ('status', []),
                             ('engine', []), ('seed', True), ('rng_run', 2147483648)):
            runs = copy.deepcopy(self.runs)
            runs[0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                summarize(runs)
        for runs in (None, {}, [], [None], [True], [self.runs[0]] * 26):
            with self.subTest(runs=type(runs)), self.assertRaises(ValueError):
                summarize(runs)

    def test_duplicate_run_identity_cannot_count_as_independent_replications(self):
        self.runs[1]['run_id'] = self.runs[0]['run_id']
        with self.assertRaisesRegex(ValueError, 'reuse'):
            summarize(self.runs)

    def test_output_scenario_duration_and_sampling_must_match_recorded_inputs(self):
        for field, value in (('duration', 99), ('duration', True), ('duration', 10**400),
                             ('scenario', {'duration': 99}), ('scenario', []), ('sample_s', .5)):
            runs = copy.deepcopy(self.runs)
            runs[0][field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError): summarize(runs)
                with self.assertRaises(ValueError): compare(runs, cohort('candidate', [11, 22]))

    def test_verified_hashes_alone_do_not_establish_a_usable_scenario_or_runtime(self):
        for field, value in (('reproducibility', ['bad']), ('runtime', []), ('runtime', {}),
                             ('scenario_name', []), ('scenarios', []), ('recorded_scenario', []),
                             ('failures', ['bad'])):
            runs = copy.deepcopy(self.runs)
            record = runs[0]['reproducibility']
            if field == 'reproducibility':
                runs[0][field] = value
            elif field == 'scenarios':
                record['project']['scenarios'] = value
            elif field == 'recorded_scenario':
                record['project']['scenarios'][record['scenario_name']] = value
            elif field == 'failures':
                record['project']['scenarios'][record['scenario_name']]['failures'] = value
            else:
                record[field] = value
            record['project_sha256'] = digest(record['project'])
            with self.subTest(field=field), self.assertRaises(ValueError): summarize(runs)

    def test_broken_flow_containers_are_controlled_errors(self):
        for field, value in (('flows', []), ('flow', []), ('summary', None),
                             ('series', {}), ('series', [None])):
            runs = copy.deepcopy(self.runs)
            if field == 'flows': runs[0]['flows'] = value
            elif field == 'flow': runs[0]['flows']['f'] = value
            else: runs[0]['flows']['f'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError): summarize(runs)
        run = copy.deepcopy(self.runs[0])
        run['flows']['f']['series'] = [None]
        analysis = analyze(run)
        self.assertIn('sample point', analysis['flows']['f']['error'])
        self.assertFalse(analysis['flows']['f']['metrics']['avg_thr_bps']['complete'])

    def test_huge_metrics_are_missing_samples_instead_of_overflowing(self):
        self.runs[0]['flows']['f']['summary']['avg_thr_bps'] = 10**400
        metric = summarize(self.runs)['metrics'][0]
        self.assertEqual(metric['missing'], 1)
        self.assertIsNone(metric['ci95'])
        json.dumps(metric, allow_nan=False)
        with self.assertRaises(ValueError): interval([10**400])

    def test_legacy_optional_metadata_and_pending_rows_remain_supported(self):
        for run in self.runs:
            run.pop('duration')
            run.pop('scenario')
        self.assertTrue(summarize(self.runs)['complete'])
        self.runs[0].update(status='running', reproducibility=None)
        result = summarize(self.runs)
        self.assertFalse(result['complete'])
        self.assertIsNone(result['metrics'][0]['ci95'])

    def test_verifier_returns_false_for_malformed_or_non_json_manifests(self):
        for record in (None, [], ['bad'], True, {'schema': True},
                       {'schema': 1, 'project': [], 'effective_scenario': {}},
                       {'schema': 1, 'project': {'bad': object()}, 'effective_scenario': {}}):
            with self.subTest(record=type(record)): self.assertFalse(verify(record))
        self.assertTrue(verify(self.runs[0]['reproducibility']))


class ExperimentInputAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        setting = patch('lab.engines.manager.RESULTS_DIR', self.temp.name)
        setting.start()
        self.addCleanup(setting.stop)
        self.client = TestClient(create_app('demo'))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.runs = cohort('experiment', [10, 20])

    def write(self, runs):
        directory = Path(self.temp.name, 'archive')
        directory.mkdir(exist_ok=True)
        for run in runs:
            (directory / (run['run_id']+'.json')).write_text(json.dumps(run))
        self.client.app.state.runs.reindex('archive')

    def test_bad_archives_fail_json_html_and_zip_then_repair_recovers(self):
        base = '/api/experiments/archive/experiment'
        for field, value in (('replication_count', []), ('replication_index', {}),
                             ('reproducibility', []), ('flows', []), ('duration', 99)):
            runs = copy.deepcopy(self.runs)
            runs[0][field] = value
            self.write(runs)
            for suffix in ('', '/report.html', '/export.zip'):
                response = self.client.get(base+suffix)
                with self.subTest(field=field, suffix=suffix):
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertIn('error', response.json())
        self.write(self.runs)
        self.assertTrue(self.client.get(base).json()['complete'])
        self.assertEqual(self.client.get(base+'/export.zip').status_code, 200)

    def test_comparison_endpoints_share_cohort_guards(self):
        self.write(self.runs)
        candidate = cohort('candidate', [11, 22])
        candidate[0]['replication_index'] = {}
        self.write(candidate)
        body = {'project': 'archive', 'baseline_id': 'experiment', 'candidate_id': 'candidate'}
        for suffix in ('', '/report.html', '/export.zip'):
            response = self.client.post('/api/experiments/compare'+suffix, json=body)
            self.assertEqual(response.status_code, 400, response.text)
        self.write(cohort('candidate', [11, 22]))
        self.assertTrue(self.client.post('/api/experiments/compare', json=body).json()['complete'])

    def test_campaign_exports_invalid_cohort_as_raw_json_with_diagnostic(self):
        project = generate('ring', routers=3, hosts=1, flows=1)
        project['scenarios'] = {'test': {'duration': 2, 'apps': [], 'events': []}}
        body = {'project': project, 'scenario': 'test', 'repetitions': 2,
                'param': {'kind': 'link', 'link': next(iter(project['links'])), 'field': 'bw'}, 'values': [20]}
        response = self.client.post('/api/campaigns', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json()
        manager = self.client.app.state.runs
        deadline = time.monotonic()+10
        while manager.futures:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        rid = plan['conditions'][0]['run_ids'][0]
        original = manager.get(project['name'], rid)
        bad = copy.deepcopy(original)
        bad['flows'] = []
        manager._save(project['name'], bad)
        base = f"/api/campaigns/{project['name']}/{plan['campaign_id']}"
        report = self.client.get(base+'/report.json').json()
        self.assertFalse(report['analysis_complete'])
        self.assertIn('flows', report['experiments'][0]['error'])
        self.assertEqual(self.client.get(base+'/report.html').status_code, 200)
        response = self.client.get(base+'/export.zip')
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            with zipfile.ZipFile(io.BytesIO(archive.read('experiments/baseline.zip'))) as nested:
                self.assertIn('flows', nested.read('error.txt').decode())
                self.assertEqual(json.loads(nested.read('results.json'))[0]['flows'], [])
                self.assertNotIn('flow_summary.csv', nested.namelist())
        manager._save(project['name'], original)
        self.assertTrue(self.client.get(base+'/report.json').json()['analysis_complete'])
