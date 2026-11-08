# Incremental report ZIP exports — 2026-11-08

Branch `perf/stream-report-exports`, based on `main` at `f95b102` (PR #43).
API, memory tradeoffs and benchmark commands:
[stream-report-exports.md](../stream-report-exports.md).

| Check | Result |
| --- | --- |
| Existing export/experiment/comparison/campaign regressions | **57 tests passed**, 13.252 s |
| New writer tests plus related integrity tests | **29 tests passed**, 5.069 s |
| Complete backend + ML labeling + rate adapter | **518 tests passed**, 42.349 s |
| Original demo/model/API/report smoke scripts | **12 scripts passed**, `NETLAB_SKIP_NS3=1` |
| Actual IPv4/IPv6 ns-3 experiment/campaign/study suite | **4 methods passed**, 232.035 s; **20 native runs** |
| Independent ZIP reader | System `unzip -t` accepted outer and nonseekable nested ZIP64 archives |
| Syntax and whitespace | `compileall`, `git diff --check` passed |

Eight new tests cover seekable/nonseekable writer equivalence, caller-owned
target lifetime, one-pass rows, UTF-8/embedded newlines and exact counters,
storage error classification, late JSON failure preserving atomic CLI output,
additional file-export allocations below 4 MiB for 20,000 flow samples,
experiment/comparison/nested-campaign writers, HTTP content length, and bounded
benchmark input. Existing tests also cover invalid raw cohort fallback exports.

The packet suite exercised six experiment/replay runs, eight campaign runs and
six parameter-study runs, including actual nested ZIP exports, paired replay
mapping, packet-result HTML and both IP families. The checkout's sources remained
frozen during packet comparisons; this document was added afterward.

For 40,000 synthetic flow samples, the buffered implementation at `bdec21e`
used 18,603,295 traced peak bytes; incremental bytes used 812,110, and direct
file output used 471,881. All ten uncompressed entries matched by size and SHA-256.
Separate untraced elapsed times were 0.241 s, 0.530 s and 0.642 s respectively.
This is an allocation improvement with a measured JSON encoding CPU tradeoff,
not a general speedup claim. Input allocation and entry inspection are excluded;
tracemalloc does not establish process RSS. Raw observations:
[stream-export-memory-2026-11-08.json](stream-export-memory-2026-11-08.json).

To reproduce the current measurements:

```sh
python -m lab.benchmarks.export_memory --flows 20 --points 2000 --mode bytes
python -m lab.benchmarks.export_memory --flows 20 --points 2000 --mode file
python -m unittest lab.tests.test_stream_exports -v
CXX=g++ python -m unittest lab.tests.smoke_experiments \
  lab.tests.smoke_campaigns lab.tests.smoke_studies -v
```

The reference was loaded from `git show bdec21e:lab/report_export.py` in a temporary
module and passed to `benchmark(flows=20, points=2000, exporter=reference.csv_bundle)`.
It uses the same fixture and measurement method as the two current modes.

Native checks require ns-3 and `NETLAB_SKIP_NS3` unset. No privileged Mininet/FRR
test or multi-gigabyte archive experiment was performed. Input records, HTML
documents and HTTP final ZIP bytes retain their memory costs; only direct-file
writers avoid the final compressed buffer. The caller must discard a low-level
writer's partial output after failure; the report CLI stages it atomically.
