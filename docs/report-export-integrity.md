# Result report and export integrity

`run_report`, `compare_report`, `study_report` and `csv_bundle` share structural
checks in `lab/report_inputs.py`. Invalid containers or consumed numeric fields
raise `ValueError` with a path such as `results[0].links.l1[0]`. The existing API
error handler returns HTTP 400. Source archives and history metadata are unchanged;
repairing the archive allows the next request to render it again.

The checks cover flows/summaries/sample objects, links/nodes, app summaries,
availability, convergence, route events, events, notes and report metadata.
Absent or null optional sections retain their empty-section behavior. Arrays
of routes/device names must contain strings; consumed metrics must be finite
numbers or null. A malformed row is not silently removed from a cohort.

CSV/ZIP code now lives in `lab/report_export.py`. Existing imports of
`lab.report.csv_bundle` and `_csv` continue to work. CSV names, headers and valid
replay attachment paths remain unchanged. `results.json` is strict finite JSON,
preserving exact integer counters. A single result retains its object format;
multiple results use an array. An empty cohort now produces a valid ZIP with
`results.json: []` and the flow CSV headers.

Replay attachments require a verified hash manifest, a named scenario object,
a supported engine and matching comparison key/output scenario metadata. A
record with a malformed or incomplete manifest still exports its raw results,
with `replay-unavailable.txt` explaining the absent replay request. Archives
without any manifest retain the legacy CSV/raw-only behavior. Comparison
`run_map.csv` uses the same eligibility check as the actual attachments.
These hashes establish consistency rather than archive authentication.

Large integer counters retain their exact CSV/JSON values and HTML formatting.
Unrepresentable creation timestamps render as unknown. SVG tick generation is
bounded, including subnormal numbers and axes near floating-point precision
limits. Unsafe ranges omit the chart; heatmaps normalize their color range to
avoid overflow. Raw and tabular data remain available.

The report CLI writes a unique temporary file beside the destination and
atomically replaces the destination after successful rendering. HTML renders
in memory; ZIP entries write directly into the temporary file.
Rendering or replacement failure reports an argparse error and preserves an
existing output; temporary files are cleaned up. Directory input requires result
IDs to match JSON filenames, consistent with server history loading. No fsync or
power-loss durability guarantee is added.

HTML reports and the HTTP response's final ZIP bytes remain buffered in memory.
ZIP CSV rows and JSON now write incrementally, including nested campaign ZIPs;
see [stream-report-exports.md](stream-report-exports.md). Validation still walks
consumed series. Point downsampling and full validation of extension schemas
are not included.

```sh
python -m unittest lab.tests.test_report_export_integrity -v
python -m lab.tests.smoke_report
```
