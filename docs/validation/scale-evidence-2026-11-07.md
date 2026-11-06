# Scale benchmark evidence validation — 2026-11-07

Branch: `fix/scale-benchmark-evidence`, base `main` `d88b7d7`.
The pending adaptive worker and emulation lifecycle branches are not dependencies.

- **171 backend tests passed**, 13.05 s: 147 existing and 24 new scale evidence
  tests. The new module is included in CI; no remote CI result is claimed here.
- **11 IPv6 model tests passed**, with one ns-3 packet test explicitly skipped
  using `NETLAB_SKIP_NS3=1`. This includes the existing public `process_usage`
  compatibility check. Simulation engines and packet behavior were not changed.
- Synthetic process fixtures cover complete/partial reads, process exit races,
  permission errors, PID replacement, PID/start-time CPU history, namespace FRR
  discovery and separate OVS ownership. A real local Python process is sampled;
  the sampler thread is joined. These are not FRR/Mininet resource measurements.
- Injected sampler startup/discovery failures invalidate evidence. Missing flows,
  nonfinite metrics and RSS budget failures cannot pass. Invalid traffic is not
  retained in checkpoints, so a NaN cannot prevent the error JSON from being saved.
- Synthetic drivers verify startup timeout, cleanup exception, interruption,
  original-error preservation, pre-command probe timing and stopping later sizes.
  Report tests verify per-run checkpoints, atomic replacement failure, duplicate
  router-count rejection, complete-success status and signal-handler restoration.
- A real local subprocess receives SIGTERM during a synthetic measurement:
  the report retains the previous passed attempt, records the interrupted attempt,
  skips later sizes and exits 130. No privileged network is started by this test.
- The actual unprivileged CLI prerequisite probe for 50/75/100 routers returned
  **exit 2 / blocked**: root, Mininet, OVS, FRR, iperf3 and ping are unavailable.
  No 50–100-router result, host capacity or new real protocol measurement is claimed.

```sh
python -m unittest lab.tests.test_runtime_safety ml.test_labeling lab.tests.test_calibration lab.tests.test_calibration_evidence lab.tests.test_calibration_collector lab.tests.test_ospf_convergence lab.tests.test_ospf_policy lab.tests.test_traffic lab.tests.test_run_queue lab.tests.test_experiments lab.tests.test_result_index lab.tests.test_analysis_windows lab.tests.test_experiment_comparison lab.tests.test_campaigns lab.tests.test_campaign_analysis lab.tests.test_real_lock lab.tests.test_scale_benchmark -q
NETLAB_SKIP_NS3=1 python -m unittest lab.tests.smoke_ipv6 -q
```

Run the new orchestration on a dedicated root host before accepting its privileged
behavior. [scale-benchmark.md](../scale-benchmark.md) describes schema 2,
large-host commands, sampling limits and interruption semantics. Historical real
measurements and calibration constants are unchanged.
