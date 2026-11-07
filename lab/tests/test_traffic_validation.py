"""Traffic imports fail as bounded, atomic validation errors before execution."""

import copy
import csv
import math
import unittest
from unittest.mock import patch

from lab.model import parse_rate, validate_scenario
from lab.topogen import generate
from lab.traffic import (MAX_CSV_BYTES, MAX_CSV_FIELD_CHARS, csv_apps,
                         gravity_apps, import_matrix, number, rate_text)


def fixture():
    return generate("ring", routers=3, hosts=1, flows=0, ipv6=True)


class CsvBoundaryTests(unittest.TestCase):
    def test_bom_blank_lines_and_valid_quoted_records_remain_supported(self):
        text = '\ufeffsrc,dst,type,rate,id,size,dscp\r\n\r\n"h0x0","h2x0",udp,"1Mbps","a,""quoted""",128.0,4.6e1\r\n'
        app = csv_apps(text, 3)[0]
        self.assertEqual(app["id"], 'a,"quoted"')
        self.assertEqual(app["size"], 128)
        self.assertEqual(app["dscp"], 46)
        self.assertEqual(parse_rate(app["rate"]), 1e6)
        self.assertEqual((app["start"], app["stop"]), (0, 3))

    def test_unclosed_and_trailing_quote_syntax_is_not_partially_imported(self):
        for text in ('src,dst,rate\nh0x0,h2x0,"1Mbps',
                     'src,dst,rate\nh0x0,h2x0,"1Mbps"trailing\n',
                     'src,dst,rate\nh0x0,h2x0,1Mbps\nh0x0,h2x0,"1Mbps'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "CSV row"):
                csv_apps(text, 3)

    def test_header_and_row_parser_limit_errors_have_controlled_context(self):
        for text in ('x' * (MAX_CSV_FIELD_CHARS + 1) + ',src,dst\n',
                     'src,dst,type,id\nh0x0,h2x0,ping,' + 'x' * (MAX_CSV_FIELD_CHARS + 1)):
            with self.subTest(header=text[:3]), self.assertRaisesRegex(ValueError, "CSV row"):
                csv_apps(text, 3)

    def test_own_field_limit_is_enforced_without_mutating_the_global_parser_limit(self):
        before = csv.field_size_limit()
        text = 'src,dst,type,id\nh0x0,h2x0,ping,abcde\n'
        with patch('lab.traffic.MAX_CSV_FIELD_CHARS', 4), \
                patch('csv.field_size_limit', side_effect=AssertionError('process-global setter must not be called')):
            with self.assertRaisesRegex(ValueError, 'CSV row 1: field exceeds'):
                csv_apps(text, 3)
        self.assertEqual(csv.field_size_limit(), before)
        self.assertEqual(len(csv_apps('src,dst,type,id\nh0x0,h2x0,ping,' + 'a' * MAX_CSV_FIELD_CHARS, 3)[0]['id']),
                         MAX_CSV_FIELD_CHARS)

    def test_text_shape_utf8_byte_budget_and_invalid_encoding_are_value_errors(self):
        for value in (None, False, [], {}, 3, 'x' * (MAX_CSV_BYTES + 1),
                      '\U0001f600' * (MAX_CSV_BYTES // 4 + 1), '\ud800'):
            with self.subTest(type=type(value).__name__), self.assertRaises(ValueError):
                csv_apps(value, 3)

    def test_duplicate_headers_and_mismatched_columns_are_rejected(self):
        for text in ('src,dst,src\nh0x0,h2x0,h0x0\n', 'src,dst,unknown\nh0x0,h2x0,x\n',
                     'src,dst,rate\nh0x0,h2x0\n', 'src,dst,rate\nh0x0,h2x0,1Mbps,extra\n',
                     'src,dst,rate\n,h2x0,1Mbps\n', 'src,dst\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                csv_apps(text, 3)

    def test_flow_budget_and_effective_id_collisions_reject_the_whole_matrix(self):
        text = 'src,dst,rate\n' + 'h0x0,h2x0,1Mbps\n' * 3
        with patch('lab.traffic.MAX_FLOWS', 2), self.assertRaisesRegex(ValueError, 'at most 2 flows'):
            csv_apps(text, 3)
        for text in ('src,dst,rate,id\nh0x0,h2x0,1Mbps,a\nh0x0,h2x0,1Mbps,a\n',
                     'src,dst,rate,id\nh0x0,h2x0,1Mbps,matrix2\nh0x0,h2x0,1Mbps,\n'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'duplicate id'):
                csv_apps(text, 3)

    def test_csv_integer_conversion_checks_exact_decimal_values_before_conversion(self):
        for field, values in (('size', ('0', '-1', '1.000000000000000000001', '65508', 'NaN', '1e1000000')),
                              ('dscp', ('-1', '64', '46.000000000000000000001', 'Infinity', '1e1000000'))):
            for value in values:
                text = f'src,dst,rate,{field}\nh0x0,h2x0,1Mbps,{value}\n'
                with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, field):
                    csv_apps(text, 3)
        for value in ('1', '65507', '128.0', '1e3'):
            app = csv_apps('src,dst,rate,size\nh0x0,h2x0,1Mbps,' + value, 3)[0]
            self.assertIsInstance(app['size'], int)
            self.assertGreaterEqual(app['size'], 1)

    def test_csv_rates_require_native_positive_rates_and_preserve_supported_units(self):
        for value in ('0', '0.5bps', '-1Mbps', 'NaN', 'Infinity', '9' * 400 + 'Gbps', '11Gbps'):
            with self.subTest(rate=value), self.assertRaises(ValueError):
                csv_apps('src,dst,rate\nh0x0,h2x0,' + value, 3)
        for value, expected in (('1bps', 1), ('5', 5e6), ('500kbps', 5e5), ('10Gbps', 1e10)):
            app = csv_apps('src,dst,rate\nh0x0,h2x0,' + value, 3)[0]
            self.assertEqual(parse_rate(app['rate']), expected)

    def test_direct_duration_and_row_windows_require_finite_values(self):
        for value in (True, False, 0, 3601, 10 ** 1000, float('nan'), float('inf'), None, [], {}):
            with self.subTest(duration=type(value).__name__), self.assertRaises(ValueError):
                csv_apps('src,dst,type\nh0x0,h2x0,ping', value)
        for start, stop in (('nan', '3'), ('0', 'inf'), ('0', '1e1000'), ('3', '3'), ('-1', '3'), ('0', '4')):
            with self.subTest(start=start, stop=stop), self.assertRaises(ValueError):
                csv_apps(f'src,dst,type,start,stop\nh0x0,h2x0,ping,{start},{stop}', 3)

    def test_unpaced_tcp_and_ping_still_omit_the_rate_field(self):
        apps = csv_apps('src,dst,type\nh0x0,h2x0,tcp\nh0x0,h2x0,ping', 3)
        self.assertTrue(all('rate' not in app for app in apps))


class GravityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.project = fixture()

    def test_direct_project_and_duration_inputs_are_validated(self):
        for project in (None, [], {}, {**self.project, 'devices': []}):
            with self.subTest(project=type(project).__name__), self.assertRaises(ValueError):
                gravity_apps(project, 3, 1)
        for duration in (True, 0, float('inf'), 10 ** 1000):
            with self.subTest(duration=type(duration).__name__), self.assertRaises(ValueError):
                gravity_apps(self.project, duration, 1)

    def test_invalid_weight_shapes_numbers_and_selection_fail_before_pair_work(self):
        for weights in ([], True, {'h0x0': 1}, {'h0x0': 1, 'missing': 1}, {'r0': 1, 'h0x0': 1},
                        {1: 1, 'h0x0': 1}, {'h0x0': True, 'h2x0': 1},
                        {'h0x0': 0, 'h2x0': 1}, {'h0x0': float('nan'), 'h2x0': 1},
                        {'h0x0': float('inf'), 'h2x0': 1}, {'h0x0': 10 ** 1000, 'h2x0': 1},
                        {str(i): 1 for i in range(101)}):
            with self.subTest(weights=weights), patch('lab.traffic.math.fsum', side_effect=AssertionError('no pairs')):
                with self.assertRaises(ValueError):
                    gravity_apps(self.project, 3, 1, weights)

    def test_total_values_and_sub_native_shares_never_get_a_hidden_rate_floor(self):
        for total in (True, False, 0, 10001, float('nan'), float('inf'), 10 ** 1000):
            with self.subTest(total=type(total).__name__), self.assertRaises(ValueError):
                gravity_apps(self.project, 3, total)
        for total, weights in ((1e-9, {'h0x0': 1, 'h2x0': 1}),
                               (1, {'h0x0': 1, 'h1x0': 1, 'h2x0': 1e9})):
            with self.subTest(total=total), self.assertRaisesRegex(ValueError, 'rate bps'):
                gravity_apps(self.project, 3, total, weights)

    def test_gravity_formatting_conserves_demand_and_determinism(self):
        weights = {'h2x0': 3, 'h0x0': 1, 'h1x0': 2}
        before = copy.deepcopy(self.project)
        apps = gravity_apps(self.project, 3, 1.23456789, weights)
        self.assertEqual(apps, gravity_apps(self.project, 3, 1.23456789, dict(reversed(list(weights.items())))))
        self.assertEqual(len(apps), 6)
        self.assertTrue(all(app['src'] != app['dst'] for app in apps))
        offered = math.fsum(parse_rate(app['rate']) for app in apps)
        self.assertTrue(math.isclose(offered, 1.23456789e6, rel_tol=1e-12, abs_tol=1e-8))
        self.assertEqual(self.project, before)
        self.assertEqual(weights, {'h2x0': 3, 'h0x0': 1, 'h1x0': 2})
        boundary = gravity_apps(self.project, 3, 2e-6, {'h0x0': 1, 'h2x0': 1})
        self.assertEqual([parse_rate(app['rate']) for app in boundary], [1, 1])


class MatrixEntryTests(unittest.TestCase):
    def test_bad_option_shapes_and_huge_numeric_values_raise_value_errors(self):
        project = fixture()
        for options in (None, [], True, 1, {'duration': 10 ** 1000}, {'duration': True},
                        {'mode': 'gravity', 'total_mbps': 10 ** 1000}):
            with self.subTest(options=type(options).__name__), self.assertRaises(ValueError):
                import_matrix(project, options)
        for value in (True, float('nan'), float('inf'), 10 ** 1000):
            with self.subTest(value=type(value).__name__), self.assertRaises(ValueError):
                number(value, 'value', 0, 10)
        for value in (True, .5, 10 ** 1000):
            with self.subTest(rate=type(value).__name__), self.assertRaises(ValueError):
                rate_text(value)

    def test_nullable_project_copy_and_failed_rows_never_mutate_the_requested_document(self):
        project = fixture()
        project['controllers'] = None
        project['devices']['r0']['config']['startup'] = None
        project['devices']['h0x0']['config']['interfaces']['h0x0-eth1']['mac'] = None
        project['extension'] = {'opaque': [None, {'version': 'future'}]}
        before = copy.deepcopy(project)
        options = {'csv': 'src,dst,rate,size\nh0x0,h2x0,1Mbps,1', 'duration': 3, 'ip_version': 6}
        result = import_matrix(project, options)
        self.assertEqual(result['count'], 1)
        self.assertEqual(validate_scenario(project, result['scenario']), [])
        for text in ('src,dst,rate\nh0x0,h2x0,1Mbps\nh0x0,missing,1Mbps',
                     'src,dst,rate\nh0x0,h2x0,1Mbps\nh0x0,h2x0,"unterminated'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                import_matrix(project, {'csv': text})
        self.assertEqual(project, before)
        self.assertEqual(options['csv'], 'src,dst,rate,size\nh0x0,h2x0,1Mbps,1')


if __name__ == '__main__':
    unittest.main()
