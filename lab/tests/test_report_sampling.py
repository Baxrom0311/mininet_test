import copy
import csv
from html.parser import HTMLParser
import io
import json
import random
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from lab.benchmarks.report_charts import fixture
from lab.report import csv_bundle, run_report, study_report, svg_lines
from lab.report_sampling import envelope, sample_series
from lab.server import create_app


class Geometry(HTMLParser):
    def __init__(self):
        super().__init__()
        self.coordinates = 0
        self.figures = []
        self.paths = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'figure': self.figures.append(attrs)
        if tag == 'polyline':
            self.paths.append(attrs['points'])
            self.coordinates += len(attrs['points'].split())
        if tag == 'circle': self.coordinates += 1


class EnvelopeTests(unittest.TestCase):
    def test_small_series_is_exact_and_one_shot_sources_are_consumed_once(self):
        seen = []
        source = [(None, 1), (1, 0), (2, 5), (3, None), (4, -1)]
        def points():
            for index, point in enumerate(source):
                seen.append(index)
                yield point
        selected, omitted = sample_series([('f', points())])
        data = selected[0][1]
        self.assertEqual(omitted, 0)
        self.assertEqual(seen, list(range(5)))
        self.assertEqual(data.observed, 5)
        self.assertEqual(data.points, [(1,1,0,0),(2,2,5,0),(4,4,-1,1)])

    def test_long_signal_keeps_endpoints_extrema_and_source_order(self):
        source = [(i, -1000 if i == 2345 else 9000 if i == 6789 else i % 17) for i in range(100000)]
        before = list(source)
        data = envelope(iter(source), 64)
        self.assertLessEqual(len(data.points), 64)
        self.assertEqual([data.points[0][0], data.points[-1][0]], [0,99999])
        self.assertIn((2345,2345,-1000,0), data.points)
        self.assertIn((6789,6789,9000,0), data.points)
        self.assertEqual([p[0] for p in data.points], sorted({p[0] for p in data.points}))
        self.assertEqual((data.xmin,data.xmax,data.ymin,data.ymax), (0,99999,-1000,9000))
        self.assertEqual(source, before)

    def test_envelope_invariants_across_merges_and_irregular_x_values(self):
        rng = random.Random(42)
        for limit in (4,8,31,1200):
            for count in (0,1,limit,limit+1,limit*4+7):
                source = [((i*37)%101, None if i%7 == 3 else rng.randrange(-100,100)) for i in range(count)]
                finite = [(i,x,y) for i,(x,y) in enumerate(source) if y is not None]
                data = envelope(source, limit)
                self.assertLessEqual(len(data.points), limit)
                if not finite:
                    self.assertEqual(data.points, [])
                    continue
                self.assertEqual((data.points[0][0], data.points[-1][0]), (finite[0][0], finite[-1][0]))
                self.assertEqual(min(p[2] for p in data.points), min(p[2] for p in finite))
                self.assertEqual(max(p[2] for p in data.points), max(p[2] for p in finite))
                self.assertEqual((data.xmin,data.xmax), (min(p[1] for p in finite),max(p[1] for p in finite)))
                for a,b in zip(data.points,data.points[1:]):
                    if a[3] == b[3]:
                        self.assertTrue(all(y is not None for _,y in source[a[0]:b[0]+1]))

    def test_many_gaps_remain_disconnected_without_unbounded_output(self):
        points = ((i, 1 if i%2 else None) for i in range(100000))
        html = svg_lines([('alternating', points)], 'test')
        geometry = Geometry()
        geometry.feed(html)
        self.assertEqual(geometry.paths, [])
        self.assertLessEqual(geometry.coordinates, 1200)
        self.assertGreater(geometry.coordinates, 0)
        self.assertIn('minimum/maksimum', html)
        self.assertIn('CSV/JSON', html)

    def test_chart_shares_point_budget_and_discloses_omitted_nonempty_series(self):
        series = [('empty', [(0,None)])]
        series.extend((f'f{i}', ((x, x%17) for x in range(200))) for i in range(129))
        html = svg_lines(series, '<chart>')
        geometry = Geometry()
        geometry.feed(html)
        self.assertLessEqual(geometry.coordinates, 12000)
        self.assertEqual(geometry.figures[0]['data-omitted-series'], '1')
        self.assertEqual(int(geometry.figures[0]['data-rendered-points']), geometry.coordinates)
        self.assertIn('yana 1 qator', html)
        self.assertIn('&lt;chart&gt;', html)

    def test_invalid_limits_and_nonfinite_samples_are_handled(self):
        for limit in (True,0,3,1201,1.5):
            with self.assertRaises(ValueError): envelope([], limit)
        data = envelope([(0,1),(1,float('nan')),(float('inf'),2),(3,2)])
        self.assertEqual([p[3] for p in data.points], [0,1])
        self.assertEqual(data.valid, 2)

    def test_long_missing_spans_do_not_discard_a_small_set_of_valid_samples(self):
        data = envelope(((i, i if i%100 == 0 else None) for i in range(10000)), 1200)
        self.assertEqual(data.observed, 10000)
        self.assertEqual(data.valid, 100)
        self.assertEqual(len(data.points), 100)
        self.assertEqual([p[0] for p in data.points], list(range(0,10000,100)))


class ReportSamplingTests(unittest.TestCase):
    def test_rendering_preserves_raw_samples_and_csv_exports(self):
        run = fixture(5000)
        before = copy.deepcopy(run)
        archive_before = csv_bundle(run)
        html = run_report(run)
        self.assertEqual(run, before)
        self.assertIn('nuqta tanlandi', html)
        self.assertLess(len(html.encode()), 100000)
        with zipfile.ZipFile(io.BytesIO(archive_before)) as a, zipfile.ZipFile(io.BytesIO(csv_bundle(run))) as b:
            self.assertEqual({name:a.read(name) for name in a.namelist()}, {name:b.read(name) for name in b.namelist()})

    def test_study_lines_keep_their_own_values_with_lazy_generation(self):
        runs = [{'run_id':f'r{i}', 'status':'done','engine':'analytic','study_id':'study','study_index':i,
                 'study_value':i,'flows':{'f1':{'summary':{'avg_thr_bps':(i+1)*1e6}},
                                        'f2':{'summary':{'avg_thr_bps':(i+1)*10e6}}}} for i in range(3)]
        observed = []
        def chart(series, *args, **kwargs):
            values = [(name,list(points)) for name,points in series]
            if args[0].startswith("O'tkazuvchanlik"):
                observed.extend(values)
            return svg_lines(values,*args,**kwargs)
        with patch('lab.report.svg_lines', side_effect=chart): study_report(runs)
        self.assertEqual(observed, [('f1',[(0,1),(1,2),(2,3)]),('f2',[(0,10),(1,20),(2,30)])])

    def test_html_api_is_bounded_while_raw_and_zip_keep_every_sample(self):
        with tempfile.TemporaryDirectory() as td, patch('lab.engines.manager.RESULTS_DIR',td), \
                TestClient(create_app('demo')) as client:
            run = fixture(10000)
            client.app.state.runs._save('history',run)
            base = '/api/runs/history/display'
            response = client.get(base+'/report.html')
            self.assertEqual(response.status_code,200,response.text)
            geometry = Geometry()
            geometry.feed(response.text)
            self.assertEqual(len(geometry.figures),3)
            self.assertTrue(all(int(f['data-rendered-points']) <= 1200 for f in geometry.figures))
            self.assertEqual(len(client.get(base).json()['flows']['signal']['series']),10000)
            response = client.get(base+'/export.zip')
            self.assertEqual(response.status_code,200)
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                self.assertEqual(len(json.loads(archive.read('results.json'))['flows']['signal']['series']),10000)
                self.assertEqual(len(list(csv.DictReader(io.StringIO(archive.read('flows.csv').decode())))),10000)
