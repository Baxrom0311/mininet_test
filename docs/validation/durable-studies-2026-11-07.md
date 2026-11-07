# Durable parameter studies validation — 2026-11-07

Branch: `fix/durable-study-admission`, base `main` `b7d3d3b`.
Pending sampling, analytical TCP and ns-3 process lifecycle branches are not dependencies.

- **249 backend tests passed**, 26.69 s: 201 existing and 48 preparation,
  admission, notification, dispatch-group and status/report regressions.
- Reproduced baseline failures: a late invalid codec was accepted; a closed queue
  returned a study ID with zero durable rows; a full queue returned success before
  its producer failed. These cases now reject synchronously before engine work.
- New tests verify upfront persistence, later-variant cancellation, shutdown,
  real child-process abrupt exit/recovery of every planned row, 2D metadata and
  project immutability, transactional storage/scheduling failure handling, and
  per-study ns-3 limits that leave unrelated workers available.
- Fixed mode retains manual global capacity while group admission remains active.
  Tests cover zero available RAM/high CPU in fixed mode, group metadata cleanup,
  conflicting limits, asynchronous dispatch failure and single-live-network
  admission without blocking unrelated work.
- Review reproduced an already-failed first Future publishing completion before
  later variants were scheduled. The submission-complete guard fixes this;
  partial/all-immediate failure tests require one notification only after admission.
  A metadata-read failure test retains local ownership for orphan diagnosis.
- Status/report tests cover known/legacy plans, pending/failed/cancelled/interrupted
  rows, missing/invalid metadata, local orphans, foreign-worker cancellation
  rejection, escaped errors, failed heatmap axes and authenticated API actions.
- **All 12 original demo/analytic smoke suites passed** with `NETLAB_SKIP_NS3=1`:
  demo, BGP, STP, firewall, multicast, VRRP, LAG, survivability, failures,
  performance, report and N-2 survivability.
- **Four actual ns-3 packet test methods passed**, 184.33 s, totaling **20 queued
  ns-3 runs**: IPv4 2D four-variant study, IPv6 1D two-variant study, experiment
  replication/replay and campaign effects. Study tests verify canonical complete
  state, every durable ID, flat/grid indices, captured mutated inputs, actual
  bandwidth/rate effects, fractional sample intervals and HTML reports.
  Source files were held fixed during these packet tests.

```sh
python -m unittest lab.tests.test_runtime_safety ml.test_labeling lab.tests.test_calibration lab.tests.test_calibration_evidence lab.tests.test_calibration_collector lab.tests.test_ospf_convergence lab.tests.test_ospf_policy lab.tests.test_traffic lab.tests.test_run_queue lab.tests.test_experiments lab.tests.test_result_index lab.tests.test_analysis_windows lab.tests.test_experiment_comparison lab.tests.test_campaigns lab.tests.test_campaign_analysis lab.tests.test_real_lock lab.tests.test_scale_benchmark lab.tests.test_adaptive_workers lab.tests.test_emulation_workers lab.tests.test_study_preparation lab.tests.test_study_queue lab.tests.test_study_reports lab.tests.test_study_notifications lab.tests.test_dispatch_groups -q
CXX=g++ python -m unittest lab.tests.smoke_studies lab.tests.smoke_experiments lab.tests.smoke_campaigns -v
```

CI includes the new unit modules and packet study suite; no remote CI result is
claimed here. No privileged Mininet/FRR study was run. The
[study guide](../parameter-studies.md) documents atomic API admission versus the
legacy producer, canonical completeness, best-effort cancellation, owner recovery,
normalization rules and existing one-server/PID-reuse limitations.
