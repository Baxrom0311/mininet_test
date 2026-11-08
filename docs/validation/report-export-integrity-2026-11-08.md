# Result report/export integrity — 2026-11-08

Branch `fix/report-export-integrity`, based on `main` at `30952df` (PR #42,
including the experiment-input and cancellation corrections).
Behavior and limits: [report-export-integrity.md](../report-export-integrity.md).

| Check | Result |
| --- | --- |
| Focused report, experiment, comparison, study and campaign tests | **66 tests passed**, 14.642 s |
| Complete backend + ML labeling + rate adapter | **510 tests passed**, 41.691 s |
| Original demo/model/API/report smoke scripts | **12 scripts passed**, `NETLAB_SKIP_NS3=1` |
| Actual IPv4/IPv6 ns-3 experiment/campaign/study suite | **4 methods passed**, 246.294 s; **20 native runs** |
| Syntax and whitespace | `compileall`, `git diff --check` passed |

Ten new tests cover path-specific errors in every result report renderer,
null/absent optional sections, no input mutation, empty cohort ZIPs, exact uint64
CSV/JSON values and huge timestamps, strict JSON, incomplete replay manifests,
extreme SVG ranges under a subprocess timeout, atomic CLI replacement/failure,
directory filename identity, API errors and recovery after archive repair.

Before the change, malformed link/availability samples raised `AttributeError`,
route arrays raised `TypeError`, oversized creation timestamps raised
`OverflowError`, and empty ZIP input raised `IndexError`. The explicit checks
now identify the consumed field rather than returning an unhandled exception.

The native suite exercised six experiment/replay runs, eight campaign runs and
six parameter-study runs. Campaign checks now also open actual nested export
ZIPs, inspect CSV rows and replay request files, verify paired `run_map.csv`
references, and render standalone packet-result HTML for both IP families.
The packet suite completed with sources frozen. The original smoke suite ran
concurrently during part of it, so these wall times are validation timings rather
than standalone performance benchmarks. This report was added afterward.

```sh
python -m unittest lab.tests.test_report_export_integrity -v
NETLAB_SKIP_NS3=1 python -m lab.tests.smoke_report
CXX=g++ python -m unittest lab.tests.smoke_experiments \
  lab.tests.smoke_campaigns lab.tests.smoke_studies -v
```

Native checks require ns-3 and `NETLAB_SKIP_NS3` unset. No privileged Mininet/FRR
test or power-loss durability experiment was performed. Reports/ZIPs remain
buffered; this change adds neither streaming export nor complete validation of
arbitrary extension schemas.
