import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from lab.server import create_app
from lab.topogen import generate


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        setting = patch('lab.engines.manager.RESULTS_DIR', self.temp.name)
        setting.start()
        self.addCleanup(setting.stop)
        self.client = TestClient(create_app('demo'))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.manager = self.client.app.state.runs
        self.project = generate('ring', routers=3, hosts=1, flows=1, ipv6=True)

    def report(self, engine='ns3', project=None, scenario='asosiy'):
        response = self.client.post('/api/scenario/preflight', json={
            'project': project or self.project, 'scenario': scenario, 'engine': engine})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_read_only_preparation_and_availability_are_separate(self):
        before = copy.deepcopy(self.project)
        files = sorted(str(p) for p in Path(self.temp.name).rglob('*'))
        with patch('lab.engines.manager._ns3_available', return_value=False), patch.object(self.manager, '_dir', side_effect=AssertionError('write')):
            report = self.report()
        self.assertTrue(report['valid'])
        self.assertTrue(report['compatible'])
        self.assertFalse(report['available'])
        self.assertFalse(report['ready'])
        self.assertEqual(report['counts']['applications'], 1)
        self.assertEqual(files, sorted(str(p) for p in Path(self.temp.name).rglob('*')))
        self.assertEqual(self.project, before)
        self.assertFalse(self.manager.active)

    def test_vlan_blockers_prevent_all_admission_paths(self):
        p = json.loads((Path(__file__).resolve().parents[1] / 'examples/vlan_lab.json').read_text())
        scenario = next(iter(p['scenarios']))
        report = self.report(project=p, scenario=scenario)
        self.assertTrue(report['valid'])
        self.assertFalse(report['compatible'])
        self.assertTrue(any(i['code'] == 'ns3.vlan_trunk' for i in report['issues']))
        with patch('lab.engines.manager._ns3_available', return_value=True), patch('lab.engines.ns3_process.run_ns3') as child:
            for route in ('/api/runs', '/api/studies', '/api/experiments', '/api/campaigns'):
                response = self.client.post(route, json={'project': p, 'scenario': scenario, 'engine': 'ns3',
                    'repetitions': 2, 'param': {'kind': 'link', 'link': next(iter(p['links'])), 'field': 'bw'}, 'values': [10, 20]})
                self.assertEqual(response.status_code, 400, response.text)
            child.assert_not_called()
        self.assertEqual(self.manager.store.list(p['name']), [])

    def test_documentation_prefix_guard_is_engine_and_family_specific(self):
        for dev in self.project['devices'].values():
            for interface in dev['config'].get('interfaces', {}).values():
                interface['ip6'] = '2001:db8::1/64'
        self.assertFalse(self.report(scenario='asosiy_ipv6')['compatible'])
        self.assertTrue(self.report(scenario='asosiy')['compatible'])
        self.assertTrue(self.report(engine='analytic', scenario='asosiy_ipv6')['compatible'])

    def test_fast_boolean_environment_and_wifi_effective_settings(self):
        for value in ('false', 1, [], None):
            self.project['scenarios']['asosiy']['ns3_fast'] = value
            self.assertFalse(self.report()['valid'])
        self.project['scenarios']['asosiy']['ns3_fast'] = False
        with patch.dict('os.environ', {'NETLAB_NS3_FAST': '1'}):
            report = self.report()
            self.assertTrue(report['effective']['fast_enabled'])
            self.assertEqual(report['effective']['mtu'], 9000)
            p = json.loads((Path(__file__).resolve().parents[1] / 'examples/wifi_ofis.json').read_text())
            report = self.report(project=p, scenario=next(iter(p['scenarios'])))
            self.assertFalse(report['effective']['fast_enabled'])
            self.assertTrue(report['effective']['fast_disabled_by_wifi'])

    def test_advisories_are_bounded_and_do_not_block_valid_runs(self):
        p = json.loads((Path(__file__).resolve().parents[1] / 'examples/nat_dhcp_lab.json').read_text())
        for link in p['links'].values():
            link['params']['jitter'] = '2ms'
        report = self.report(project=p, scenario=next(iter(p['scenarios'])))
        self.assertTrue(report['compatible'])
        self.assertTrue(any(i['code'] == 'ns3.jitter_ignored' for i in report['issues']))
        self.assertTrue(all(len(i.get('paths', [])) <= 10 for i in report['issues']))

    def test_invalid_input_and_unknown_engine_are_safe_reports(self):
        for body in ({}, {'project': [], 'scenario': 'x'}, {'project': self.project, 'scenario': 'asosiy', 'engine': []},
                     {'project': self.project, 'scenario': 'asosiy', 'engine': 'unknown'}):
            response = self.client.post('/api/scenario/preflight', json=body)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['ready'])
            self.assertFalse(response.json()['valid'])

    def test_application_advisories_reach_api_for_every_engine(self):
        p = copy.deepcopy(self.project)
        original = p['scenarios']['asosiy']['apps'][0]
        p['scenarios']['asosiy']['apps'] = [
            {'id': kind, 'type': kind, 'src': original['src'], 'dst': original['dst'], 'start': 0, 'stop': 5}
            for kind in ('http', 'http3', 'video', 'ftp', 'voip')]
        before = copy.deepcopy(p)
        for engine in ('analytic', 'ns3', 'mininet'):
            with self.subTest(engine=engine):
                report = self.report(engine=engine, project=p)
                self.assertTrue(report['valid'], report)
                self.assertTrue(report['compatible'], report)
                warnings = {i['code']: i for i in report['issues'] if i['code'].startswith('application.')}
                self.assertEqual(len(warnings), 5)
                self.assertTrue(all(i['severity'] == 'warning' for i in warnings.values()))
                self.assertIn('0-RTT', warnings['application.http3_model']['message'])
                self.assertIn('after the run', warnings['application.video_model']['message'])
        self.assertEqual(p, before)

    def test_application_advisories_are_grouped_bounded_and_read_only(self):
        from lab.engines.preflight import application_issues
        sc = {'apps': [{'id': f'a{i}', 'type': 'http3'} for i in range(100)]}
        before = copy.deepcopy(sc)
        warnings = application_issues(sc)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]['count'], 100)
        self.assertEqual(len(warnings[0]['paths']), 10)
        self.assertEqual(warnings[0]['paths'][0], 'scenario.apps.0')
        self.assertEqual(sc, before)
        self.assertEqual(application_issues({'apps': [{'type': t} for t in ('tcp', 'udp', 'ping', 'mcast')]}), [])

    def test_completed_results_preserve_application_caveats_and_engine_notes(self):
        from lab.engines.preflight import application_issues
        p = copy.deepcopy(self.project)
        original = p['scenarios']['asosiy']['apps'][0]
        sc = {'duration': 5, 'apps': [
            {'id': 'stream', 'type': 'http3', 'src': original['src'], 'dst': original['dst'], 'start': 0, 'stop': 5}],
              'events': []}
        p['scenarios']['modeled'] = sc
        for engine in ('analytic', 'ns3', 'mininet'):
            with self.subTest(engine=engine), \
                    patch('lab.engines.analytic.run', return_value={'flows': {}, 'notes': ['engine note']}), \
                    patch.object(self.manager, '_run_ns3', return_value={'flows': {}, 'notes': ['engine note']}), \
                    patch('lab.engines.emulation.run', return_value={'flows': {}, 'notes': ['engine note']}):
                self.manager._worker(f'caveat-{engine}', p['name'], p, 'modeled', sc, engine)
                result = self.manager.get(p['name'], f'caveat-{engine}')
                self.assertEqual(result['status'], 'done')
                self.assertEqual(result['application_warnings'], application_issues(sc))
                self.assertIn('engine note', result['notes'])
                self.assertIn(application_issues(sc)[0]['message'], result['notes'])
