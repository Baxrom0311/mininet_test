# Analytical TCP solver validation — 2026-11-07

Branch: `fix/analytic-tcp-solver`, base `main` `b7d3d3b`.
The pending subsecond sampling and ns-3 process lifecycle branches are not dependencies.

- **220 backend tests passed**, 18.49 s: 201 existing and 19 new allocator,
  public-engine and benchmark tests. CI includes the three new modules.
- Public-engine baseline regressions reproduced three errors: 64 finite files
  completed only 32; 100 rate caps stopped increasing at 64 Mbps; a requested
  2-byte transfer reported 1251 bytes. All now pass, along with application-order
  invariance, UDP residual capacity, saturated-UDP behavior and completed-file
  capacity reassignment to an ongoing stream.
- Helper tests cover weighted fairness, overlapping bottlenecks, duplicate
  resource keys, shared Wi-Fi airtime units, zero budgets/caps, resource-free
  flows, 256 cap levels, 128 completions, submicrosecond transfers, simultaneous
  completion and the unchanged setup reservation approximation.
- **1000-flow public-engine benchmark passed**. The finite case completed
  1000/1000 files and requested/delivered exactly **500500000 bytes**, in 1.852 s.
  The distinct rate-cap case matched all 1000 requested rates in .976 s.
  [Raw JSON](analytic-tcp-benchmark-2026-11-07.json) explicitly marks these as
  synthetic fluid-model results, not real network or host capacity measurements.
- **All 12 original demo/analytic smoke suites passed** with `NETLAB_SKIP_NS3=1`:
  demo, BGP, STP, firewall, multicast, VRRP, LAG, survivability, failures,
  performance, report and N-2 survivability.
- **Two actual ns-3 packet suites passed**, 124.94 s: IPv4/IPv6 experiment/replay
  and campaign effects, totaling 14 queued ns-3 runs. Source was held fixed during
  these tests. They verify compatibility of the shared run/report workflow;
  the TCP solver change itself affects only the analytical engine.

```sh
python -m unittest lab.tests.test_runtime_safety ml.test_labeling lab.tests.test_calibration lab.tests.test_calibration_evidence lab.tests.test_calibration_collector lab.tests.test_ospf_convergence lab.tests.test_ospf_policy lab.tests.test_traffic lab.tests.test_run_queue lab.tests.test_experiments lab.tests.test_result_index lab.tests.test_analysis_windows lab.tests.test_experiment_comparison lab.tests.test_campaigns lab.tests.test_campaign_analysis lab.tests.test_real_lock lab.tests.test_scale_benchmark lab.tests.test_adaptive_workers lab.tests.test_emulation_workers lab.tests.test_tcp_fluid lab.tests.test_analytic_tcp lab.tests.test_analytic_tcp_benchmark -q
python -m lab.benchmarks.analytic_tcp --flows 1000 --output /tmp/analytic-tcp.json
CXX=g++ python -m unittest lab.tests.smoke_experiments lab.tests.smoke_campaigns -v
```

No privileged FRR/Mininet test, TCP protocol calibration, hardware speedup or remote
CI result is claimed. The [model guide](../analytic-tcp.md) describes allocation
and payload conservation, existing setup reservation, display rounding and
variable computational cost as the number of distinct events grows.
