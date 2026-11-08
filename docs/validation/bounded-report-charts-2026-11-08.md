# Bounded HTML report charts — 2026-11-08

Branch `perf/bounded-report-charts`, based on `main` at `efc5d9c` (PR #44).
Algorithm, display limits and remaining costs:
[bounded-report-charts.md](../bounded-report-charts.md).

| Check | Result |
| --- | --- |
| Complete backend + ML labeling + rate adapter | **528 tests passed**, 39.697 s |
| Original demo/model/API/report smoke scripts | **12 scripts passed**, `NETLAB_SKIP_NS3=1` |
| Actual IPv4/IPv6 ns-3 experiment/campaign/study suite | **4 methods passed**, 225.359 s; **20 native runs** |
| Syntax and whitespace | `compileall`, `git diff --check` passed |
| Headless browser screenshot | **Blocked**: Chromium 155 timed out twice; an empty-page diagnostic also crashed |

Ten new tests cover exact small-series output and single-pass iterators, global
extrema/endpoints/order through repeated envelope merges, irregular x values,
many disjoint segments, per-chart point/series budgets and omission notices,
nonfinite points, long missing spans with few finite values, raw/CSV preservation,
independent lazy study lines, and API HTML reduction with all 10,000 source
samples still present in raw JSON and ZIP.

The native suite exercised six experiment/replay runs, eight campaign runs and
six parameter-study runs. Actual packet-result HTML, study reports, nested
campaign ZIPs and paired replay mappings passed for both IP families. Those
small packet series establish compatibility; large-series reduction uses the
synthetic and API fixtures. Sources remained frozen during packet comparisons;
this validation document was added afterward.

For one synthetic flow with 100,000 samples per metric and three charts, HTML
decreased from 3,582,596 bytes to 22,295. Additional traced Python peak decreased
from 28,805,872 bytes to 317,390. Separate untraced render times were 0.864 s and
0.575 s. Input hashes matched before and after both renders. Input allocation
and hashing are excluded; this is not RSS, simulator throughput, or a general
performance guarantee. Raw measurements:
[report-chart-memory-2026-11-08.json](report-chart-memory-2026-11-08.json).

The reference module came from `git show a3573be:lab/report.py`; its `run_report`
was passed to `lab.benchmarks.report_charts.benchmark(100000, renderer=...)`.
Current checks can be reproduced with:

```sh
python -m lab.benchmarks.report_charts --points 100000
python -m unittest lab.tests.test_report_sampling -v
NETLAB_SKIP_NS3=1 python -m lab.tests.smoke_report
CXX=g++ python -m unittest lab.tests.smoke_experiments \
  lab.tests.smoke_campaigns lab.tests.smoke_studies -v
```

Visual browser verification is not claimed. The installed Chromium
`155.0.8059.79` produced no screenshot in a normal headless attempt or an isolated
profile/software-rendering attempt (timeouts, exit 124). An independent
`about:blank` attempt with no zygote/single process exited 133. Logs included
Mojo/zygote/DBus errors; their root cause was not established. HTML structure,
SVG coordinates, gap segmentation and escape handling were checked in tests.

No privileged Mininet/FRR test was performed. Display reduction does not retain
every oscillation or short disconnected segment; figures disclose reduction and
omitted series. Full tables, raw results, CSV/JSON and numerical analysis retain
the original data. Input memory, tables, heatmaps and long labels remain costs.
