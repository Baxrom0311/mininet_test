# Bounded SVG report charts

Standalone HTML line charts now reduce displayed geometry while retaining full
result records and statistics. CSV/JSON exports, analysis windows, confidence
intervals, availability and convergence calculations use the original samples.

Each chart displays the first 128 data-bearing series in input order, sharing
a budget of 12,000 coordinates with at most 1,200 per line. Empty series do not
consume a slot. The figure states how many additional series were omitted.
Selected lines whose finite samples exceed their assigned budget show retained
and source sample counts and refer readers to the complete CSV/JSON export.
Machine-readable figure attributes provide `data-source-points`,
`data-rendered-points` and `data-omitted-series`.

`lab/report_sampling.py` builds an envelope in one pass. It retains bin
first/last/minimum/maximum samples in their original order and coarsens adjacent
bins when storage fills. Bins count finite samples, so long missing spans do not
discard a small set of valid samples. Series within their assigned limit retain
all finite samples. The first/last valid samples and global y extrema survive
reduction, and axis bounds use the original finite data for selected lines.

Every retained point also carries its original contiguous segment identity.
Missing/nonfinite x or y values split lines; reduction never connects retained
points across such a gap. Under heavy fragmentation, whole short segments may
be omitted within the point budget. The method does not average, interpolate,
preserve every oscillation or establish exact event timing from the displayed
curve. Raw samples and numerical analysis remain the appropriate evidence.
Absent records that contain no explicit invalid sample do not create a new gap
automatically.

Report sample iterators avoid creating full x/y lists for SVGs. Parametric study
lines keep independent lazy iterators for their own flow and metric. HTML tables,
input dictionaries, heatmaps and long scalar labels keep their existing storage
costs; this is a bound on line-chart sample geometry rather than all report size.
Existing overflow handling can still omit an unsafe numeric chart range.

## Local synthetic measurement

One flow with 100,000 samples for each of three metrics, including a peak, a
zero-throughput outage and an explicit missing sample:

| Render | HTML bytes | Additional traced Python peak | Untraced time |
| --- | ---: | ---: | ---: |
| Before (`a3573be`) | 3,582,596 | 28,805,872 | 0.864 s |
| Bounded | 22,295 | 317,390 | 0.575 s |

Input hashes matched before/after rendering. These are local synthetic display
measurements, not simulator throughput or total process RSS. Input allocation
and hashing are excluded; tracing and timing use separate renders. Exact numbers
depend on the signal and environment. Raw observations:
[report-chart-memory-2026-11-08.json](validation/report-chart-memory-2026-11-08.json).

```sh
python -m lab.benchmarks.report_charts --points 100000
python -m unittest lab.tests.test_report_sampling -v
```
