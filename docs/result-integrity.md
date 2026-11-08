# Result JSON integrity

Result retrieval, history indexing and offline reports reject nonfinite JSON
constants (`NaN`, `Infinity`, `-Infinity`) and float overflow such as `1e999`,
including inside nested flow series. Integer counters retain their exact value.
Individual result files must contain an object; manager retrieval and indexing
also require the run ID to match the filename. Offline cohort arrays remain
supported by report and comparison commands.

Invalid history files are excluded from the index without rewriting or deleting
the source archive. A repaired file can be indexed again. Direct API retrieval
returns a controlled input error rather than exposing nonfinite JSON to clients.
Legacy invalid/oversized creation timestamps normalize to zero without overflowing
the index importer.

Writers use a unique temporary file beside the result, reject nonfinite output,
and atomically replace the destination only after successful serialization.
Serialization/replace failures preserve an existing destination and clean up the
temporary file. This protects atomic visibility; it is not an fsync/power-loss
durability guarantee.

Failed persistence is reported as an error and the queue settles without leaving
a partial result file. If the complete JSON was committed before an index update
failed, the JSON remains authoritative; later history reconciliation restores its
metadata. Existing result identities and formats do not change.

Warm history scans retain the existing `(mtime_ns, size)` signature policy.
Edits that intentionally preserve both require a forced reindex; strict direct
retrieval still checks the file. See [result-index.md](result-index.md).

```sh
python -m unittest lab.tests.test_result_integrity lab.tests.test_result_index lab.tests.test_invalid_result_index -v
```
