# ns-3 subprocess lifecycle validation — 2026-11-07

Branch: `fix/ns3-process-lifecycle`, base `main` `33899ea` (adaptive workers and
scale evidence merged). The pending emulation lifecycle branch is not required.

- **208 backend tests passed**, 19.39 s: 188 existing tests and 20 subprocess,
  backlog and manager integration regressions. CI includes all three new modules.
- Local real Python children exercise success, callback failure, nonzero exit,
  absent/malformed/nonobject/nonfinite results, bad protocol fields, silent startup
  cancellation, cancellation after stdout EOF and EOF-without-exit timeout.
- Selector setup/read errors, SIGTERM resistance, cleanup signalling failures,
  inherited stdout descendants, oversized lines, invalid UTF-8 and noisy output
  with a slow callback are covered. Direct children are waited and pipes closed;
  tests do not start Mininet or import ns-3.
- A slow-consumer regression drains all 100 log messages after the child exits.
  Pipe timeout measures inactivity, excluding callback/drain time. Review found
  and corrected the initial elapsed-time implementation before final validation.
- Public manager tests verify legacy callback-failure cleanup and queued result
  JSON retaining the primary error and cleanup `error_notes`.
- **Two actual ns-3 packet tests passed**, 125.13 s: IPv4/IPv6 experiment
  replication/replay and campaign bandwidth effects, totaling 14 queued ns-3 runs.
  Exact replayed flow series, fractional sample windows and paired effects passed.
  An earlier attempt returned an incomplete campaign analysis while source files
  were still being edited. Final packet validation held source files fixed;
  runtime/source compatibility guards were not changed.

```sh
python -m unittest lab.tests.test_runtime_safety ml.test_labeling lab.tests.test_calibration lab.tests.test_calibration_evidence lab.tests.test_calibration_collector lab.tests.test_ospf_convergence lab.tests.test_ospf_policy lab.tests.test_traffic lab.tests.test_run_queue lab.tests.test_experiments lab.tests.test_result_index lab.tests.test_analysis_windows lab.tests.test_experiment_comparison lab.tests.test_campaigns lab.tests.test_campaign_analysis lab.tests.test_real_lock lab.tests.test_scale_benchmark lab.tests.test_adaptive_workers lab.tests.test_ns3_process lab.tests.test_ns3_process_backlog lab.tests.test_ns3_lifecycle_integration -q
CXX=g++ python -m unittest lab.tests.smoke_experiments lab.tests.smoke_campaigns -v
```

The helper targets Linux subprocess pipes and private process groups. Log limits
are not result-series truncation, and no protocol model or calibration constants
were changed. No privileged FRR/Mininet test or remote CI run is claimed.
See [ns3-runtime.md](../ns3-runtime.md) for timeout and ownership boundaries.
