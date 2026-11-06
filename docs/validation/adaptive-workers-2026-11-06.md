# Adaptive worker validation — 2026-11-06

Branch: `feat/adaptive-run-workers`, base `main` `d88b7d7`.

- **164 backend tests passed**, 13.95 s: 147 existing tests and 17 resource,
  dispatch and API regressions. CI now includes the new test module.
- Synthetic resource fixtures verify CPU affinity, fractional cgroup v2 quotas,
  parent memory headroom, container leaf mounts, CPU usage deltas and missing
  sensors. A simulated 16-CPU machine starts eight blocked jobs simultaneously;
  reducing capacity/load allowance limits new work without stopping those jobs.
- Tests cover growing capacity, RAM pause/resume, persistent estimated memory
  reservations, queued cancellation, engine serialization with other-engine
  bypass, dispatch failure ownership cleanup, abandoned work suppression,
  callback lock ordering, nonwaiting shutdown, manual overrides, invalid config
  and authenticated `/api/resources` responses.
- **Two actual ns-3 packet smoke tests passed**, 123.33 s, using ns-3
  3.44.post0 with `CXX=g++`: IPv4 and IPv6 experiment/replay and campaign effects.
  Together these execute 14 queued ns-3 runs. Packet throughput, fractional
  sample windows, exact replayed flow results and paired campaign effects passed.
- This cloud process reported 5 logical/affinity CPUs, a **4-CPU cgroup quota**
  and a **32 GiB memory limit**. Its initial CPU admission ceiling is 3.
  This is separate from the user's i9/64 GB host; no measurements on that host
  or performance speedup claims are made.

Backend regression command:

```sh
python -m unittest lab.tests.test_runtime_safety ml.test_labeling lab.tests.test_calibration lab.tests.test_calibration_evidence lab.tests.test_calibration_collector lab.tests.test_ospf_convergence lab.tests.test_ospf_policy lab.tests.test_traffic lab.tests.test_run_queue lab.tests.test_experiments lab.tests.test_result_index lab.tests.test_analysis_windows lab.tests.test_experiment_comparison lab.tests.test_campaigns lab.tests.test_campaign_analysis lab.tests.test_real_lock lab.tests.test_adaptive_workers -q
```

Packet validation command:

```sh
CXX=g++ python -m unittest lab.tests.smoke_experiments lab.tests.smoke_campaigns -v
```

No privileged Mininet/FRR test was run for this branch. Memory reservations are
estimates; resource detection supports Linux `/proc` and visible cgroup v2 limits.
See [adaptive-workers.md](../adaptive-workers.md) for tuning and limitations.
