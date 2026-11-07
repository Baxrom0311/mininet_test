# Analytical sampling validation — 2026-11-07

Branch: `feat/analytic-subsecond-sampling`, base `main` `b7d3d3b`.
The pending ns-3 process lifecycle branch is not a dependency.

- **234 backend tests passed**, 22.07 s: 201 existing and 33 new sampling,
  archive compatibility and fractional postprocessing tests. CI includes the
  three new modules. No remote CI result is claimed.
- Core checks cover a regular fine grid with fractional tail, flow/link/node
  interval consistency, aligned short applications, immutable inputs, weighted
  summaries, finite TCP byte conservation, progress and invalid/oversized grids.
  A queued API replication test verifies `sample_s` and complete window statistics.
- IPv4/IPv6 model tests verify a .25-second link event processed at .3 on a .1
  grid, a .3-second OSPF hold and recovery at the correct boundary. RIP/BGP/LAG
  deadline checks tolerate floating point roundoff. Wi-Fi roaming keeps its
  one-second outage at .1-second sampling.
- Review reproduced per-bin integer rounding erasing all estimated drops at
  1 ms. The corrected fluid estimate conserves 125 dropped packets and node
  discards with 50% loss across sample 1/.1/.01/.001 in the synthetic overload case.
- Archive tests keep old analytic results at one-second resolution without a
  `sample_s` marker, even if their original input requested another interval.
  New marker/input mismatches fail paired comparison. ns-3 semantics and existing
  source/runtime/input guards remain intact.
- Explicit-interval postprocessing tests cover duration-weighted VoIP/multicast,
  observed-time availability, clipping, gaps, malformed/duplicate/overlapping
  bins and FTP fallback completion. Legacy timestamp-only paths remain covered.
- **All 12 original demo/analytic smoke suites passed** with `NETLAB_SKIP_NS3=1`:
  demo, BGP, STP, firewall, multicast, VRRP, LAG, survivability, failures,
  performance, report and N-2 survivability.
- **Two actual ns-3 packet suites passed**, 134.94 s: IPv4/IPv6 experiment/replay
  and campaign effects, totaling 14 queued ns-3 runs. These ran with source held
  fixed and validate compatibility of shared report/window logic. They do not
  make the analytical engine a packet simulator.

```sh
python -m unittest lab.tests.test_runtime_safety ml.test_labeling lab.tests.test_calibration lab.tests.test_calibration_evidence lab.tests.test_calibration_collector lab.tests.test_ospf_convergence lab.tests.test_ospf_policy lab.tests.test_traffic lab.tests.test_run_queue lab.tests.test_experiments lab.tests.test_result_index lab.tests.test_analysis_windows lab.tests.test_experiment_comparison lab.tests.test_campaigns lab.tests.test_campaign_analysis lab.tests.test_real_lock lab.tests.test_scale_benchmark lab.tests.test_adaptive_workers lab.tests.test_emulation_workers lab.tests.test_analytic_sampling lab.tests.test_sampling_compatibility lab.tests.test_fractional_postprocess -q
CXX=g++ python -m unittest lab.tests.smoke_experiments lab.tests.smoke_campaigns -v
```

No real FRR/Mininet test, protocol retuning or large-host speedup is claimed.
The [sampling guide](../analytic-sampling.md) documents next-boundary quantization,
midpoint mobility/VRRP/LAG approximations, millisecond route-event display rounding,
resource bounds and compatibility rules. Historical measurement files are unchanged.
