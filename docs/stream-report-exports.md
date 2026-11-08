# ZIP exports with bounded intermediate storage

CSV rows and JSON encoder chunks now write directly into ZIP entries. The exporter
no longer creates a list of every CSV row, a full CSV string per entry or a full
raw-results JSON string. Experiment/comparison exports write their base result
entries into the same open archive. Campaigns write nested experiment archives
directly into outer ZIP entries, including the raw/error fallback for invalid
cohorts. Entries enable ZIP64 because their final size is not known in advance.

The existing `csv_bundle`, `experiment_bundle`, `comparison_bundle` and
`campaign_bundle` functions still return bytes. HTTP endpoints, authentication,
download names, content lengths and controlled validation errors retain their
contracts. These responses are prepared completely before transmission.
Entry names, CSV headers, quoting, UTF-8 content and exact integer values remain
compatible; ZIP metadata/headers may differ from previous exports.

For direct file output, use a binary target with the corresponding writer:

```python
from lab.report_export import write_csv_bundle

with open('results.zip', 'wb') as target:
    write_csv_bundle(runs, target)
```

Other writers are `lab.report.write_experiment_bundle`,
`lab.experiment_comparison_report.write_comparison_bundle` and
`lab.campaign_report.write_campaign_bundle`. Caller-owned targets remain open;
nonseekable targets are supported. Low-level writer failures leave partial
output, which callers must discard. Storage failures propagate as storage
errors rather than being mislabeled as invalid JSON.

The report CLI's `--zip` path writes directly to its unique temporary output,
then atomically replaces the destination after the archive closes successfully.
Late serialization errors and storage failures preserve any prior destination.

## Local allocation measurement

For 20 flows × 2,000 samples, comparing the buffered implementation from
`bdec21e` with this implementation on the same synthetic data:

| Export | Additional traced Python peak | Untraced elapsed time |
| --- | ---: | ---: |
| Buffered bytes | 17.74 MiB | 0.241 s |
| Incremental bytes | 0.77 MiB | 0.530 s |
| Incremental direct file | 0.45 MiB | 0.642 s |

All ten uncompressed entries had identical sizes and SHA-256 digests. The bytes
peak decreased about 23× and the direct-file peak about 39×. Incremental JSON
encoding trades CPU time for lower intermediate storage; this observation is
not a throughput guarantee. Input construction and archive inspection are
excluded. Timing uses a separate untraced export; `tracemalloc` measures a second
export. Tracing does not establish total process RSS or all native allocations.
The fixture is synthetic export data, not a Mininet/ns-3 measurement or the
user's workstation hardware. Raw observations are in
[stream-export-memory-2026-11-08.json](validation/stream-export-memory-2026-11-08.json).

```sh
python -m lab.benchmarks.export_memory --flows 20 --points 2000 --mode bytes
python -m lab.benchmarks.export_memory --flows 20 --points 2000 --mode file
python -m unittest lab.tests.test_stream_exports -v
```

## Remaining storage costs

Input result dictionaries still reside in memory. The HTTP/bytes compatibility
functions retain the final compressed ZIP, and HTML report builders still create
their document strings. Direct-file writers avoid that final ZIP buffer but
their caller controls file storage. A very large single scalar or CSV row may
still need proportional encoder storage. This change adds neither streamed HTTP
responses nor point downsampling, and does not add fsync durability guarantees.
