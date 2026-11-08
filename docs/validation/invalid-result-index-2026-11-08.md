# Invalid result index — 2026-11-08

Branch `perf/invalid-result-index`, based on `70ff40b` (`fix/result-json-integrity`).
That baseline is now merged into `main` by PR #39 (`f87dd97`), so this change can
be reviewed directly against `main`. API and limits: [result-index.md](../result-index.md).

| Check | Result |
| --- | --- |
| Complete backend + ML labeling + rate adapter, final implementation | **486 tests passed**, 29.960 s |
| Original demo/model/API/report smoke scripts | **12 scripts passed**, `NETLAB_SKIP_NS3=1` |
| Actual IPv4/IPv6 ns-3 replication/campaign/study suite, final implementation | **4 methods passed**, 177.123 s; **20 native runs** |
| Syntax and whitespace | `compileall`, `git diff --check` passed |

Nine new tests cover unchanged corrupt archives across polling and restart,
forced reparse, repair/removal, project isolation, bounded errors and paging,
database write failure, transient permission failure, preservation of active
worker metadata (including subsequent file removal), transactional cleanup on
successful save, authenticated API access and compatibility with run ID `invalid`.
The restart test makes any second parser call fail: this verifies avoided parsing
directly, rather than relying on noisy wall-time comparisons.

The original smoke scripts passed before the final active-worker cleanup guard.
That guard was then covered by the complete backend suite and native suite.
An initial native run was deliberately interrupted before applying this guard;
the counts and time above refer only to the complete rerun. Source files remained
frozen throughout the complete packet comparison run; this report was added afterward.

Reproduce focused and packet checks from the repository root:

```sh
python -m unittest lab.tests.test_invalid_result_index \
  lab.tests.test_result_integrity lab.tests.test_result_index -v
CXX=g++ python -m unittest lab.tests.smoke_experiments \
  lab.tests.smoke_campaigns lab.tests.smoke_studies -v
```

Native checks require ns-3 and `NETLAB_SKIP_NS3` unset. The packet suite exercised
six experiment/replay runs, eight campaign runs and six parameter-study runs.
No privileged Mininet/FRR test was performed in this environment.

The new SQLite table is additive; source JSON files are never moved or deleted
by reconciliation. Directory scanning still costs O(number of files), and edits
preserving both size and mtime require forced reindex. Temporary read errors are
retried rather than cached as deterministic parse failures.
