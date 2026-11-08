# Archived experiment input checks

Experiment JSON, HTML, ZIP and paired comparison endpoints validate cohort
metadata before sorting, grouping or computing statistics. Invalid result fields
produce a controlled HTTP 400 with an error message. Missing experiments still
return 404. Offline `summarize` and `compare` raise `ValueError` for these inputs.

The checks cover run/experiment IDs, distinct run IDs, replication counts and
indices, seed/substream ranges, engine/status values, manifest shape and hashes,
recorded scenario/runtime structure, flow/summary/series containers and sample
object shape. Seeds and substreams must be integers in 1..2147483647, matching
scenario admission. A self-consistent hash alone does not establish that a
manifest contains a usable scenario or runtime.

Completed results with output `scenario`, `duration` or analytic `sample_s`
metadata must agree with the recorded original scenario. Older results lacking
these optional output fields remain supported. In particular, an analytic
archive without `sample_s` retains its historical one-second grid even when its
scenario requested another interval. Pending and failed rows do not need a
completed reproducibility manifest; they keep the cohort incomplete and suppress
confidence intervals. Huge integers outside finite floating-point range count
as missing metrics, and are rejected as direct `interval()` samples.

`verify()` returns false for malformed manifests instead of raising an attribute
or serialization error. Its hashes detect input inconsistency; they do not
authenticate an archive or prove the simulation was executed.

Campaign analysis records the invalid cohort's error and suppresses dependent
comparisons. Campaign HTML and ZIP remain available. A cohort with records but
no valid summary has a nested `experiments/<variant>.zip` containing only
`results.json` (an array of original parsed records) and `error.txt`. This keeps
the evidence available without interpreting broken containers as CSV columns.
Valid cohorts retain their experiment ZIP format; cohorts with no surviving
records retain their `-unavailable.txt` entry.

The index's invalid-file diagnostics concern JSON parsing and indexed metadata.
These analysis checks do not remove archives from the history index or rewrite
their contents. They do not validate every link/node/application export field.
Flow sample object shapes are checked in one pass, without copying the series.
Numerical sample coverage remains the window analysis's responsibility; invalid
sample objects passed directly to `analyze()` yield coverage diagnostics.

```sh
python -m unittest lab.tests.test_experiment_input_integrity \
  lab.tests.test_experiments lab.tests.test_experiment_comparison \
  lab.tests.test_campaign_analysis lab.tests.test_campaigns -v
```
