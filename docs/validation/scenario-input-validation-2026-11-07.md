# Scenario input validation — 2026-11-07

Branch: `fix/scenario-input-validation`, based on `main` at `b7d3d3b`.
Contract and limits: [scenario-validation.md](../scenario-validation.md).

## Changes verified

Malformed scenario collections/items, booleans used as numbers, nonfinite values,
overflow, fractional integer fields, invalid packet sizes, zero file sizes, and
invalid windows now return validation errors before execution. Failure generation
checks both input structure and its raw interval/fan-out budget before union.
Application ID checks cover automatic IDs, derived model IDs, and the native port
budget. Model postprocessing uses the same automatic IDs as expansion and supports
the multicast `receivers` alias.

The ns-3 adapter canonicalizes rates to integer `bps`, clips stop times before
native timer construction, and clips long echo intervals outside the observation
window. Paced application rates must serialize a packet in at least 1 ns. Event
delay/jitter and move coordinates have representability bounds too.

## Environment

- Python 3.12; ns-3 `3.44.post0`, cppyy `3.5.0`, `CXX=g++`.
- Observed cloud limits: 4 CPU quota and 32 GiB RAM; these results do not measure
  the user's 64 GiB/i9/RTX3050 host.
- No `mn`, `vtysh`, or `iperf3` executable was available. No privileged Mininet,
  FRR, or physical resource-capacity claim is made.

## Results

| Check | Result |
| --- | --- |
| Complete existing backend suite + 51 new checks | **252 tests passed**, 19.640 s |
| Original demo/model/API scripts with `NETLAB_SKIP_NS3=1` | **12 scripts passed** |
| Actual ns-3 input-boundary/replication/campaign suite | **4 methods passed**, 167.812 s; **16 native runs** across IPv4 and IPv6 |
| Example compatibility and caller immutability | **17 projects / 22 scenarios accepted unchanged** |
| Invalid API admission | **33 requests rejected with HTTP 400**, no run/campaign rows, active work, or engine calls |
| Syntax, lazy imports, whitespace | `compileall`, root simulation + real driver imports, `git diff --check` passed |

The new 51 checks are 21 scenario/API tests, 13 application/multicast tests,
16 failure tests, and one native rate-adapter unit test. Cases include native
timer/rate boundaries, both kinds of derived-ID collision, a mocked small port
budget, and multicast rejection before flow materialization. Existing deterministic
failure output is compared with the prior seeded algorithm.

The native input test ran two simulations, one per address family: one-byte UDP
with numeric exponent-form rates, alternate `b/s` rate units, stops of `1e308`,
one-byte echo with a long interval, and a completed 1000-byte FTP transfer with
an automatic ID. The existing replication test ran six simulations, including
exact flow replay. Campaign tests ran eight simulations and verified positive
paired bandwidth effects. Packet comparison checks ran against one frozen source
tree; this evidence document was added afterward.

## Reproduce

Run the full backend suite in the project environment:

```sh
python -m unittest \
  lab.tests.test_runtime_safety ml.test_labeling lab.tests.test_calibration \
  lab.tests.test_calibration_evidence lab.tests.test_calibration_collector \
  lab.tests.test_ospf_convergence lab.tests.test_ospf_policy lab.tests.test_traffic \
  lab.tests.test_run_queue lab.tests.test_experiments lab.tests.test_result_index \
  lab.tests.test_analysis_windows lab.tests.test_experiment_comparison \
  lab.tests.test_campaigns lab.tests.test_campaign_analysis lab.tests.test_real_lock \
  lab.tests.test_scale_benchmark lab.tests.test_adaptive_workers \
  lab.tests.test_emulation_workers lab.tests.test_scenario_validation \
  lab.tests.test_application_validation lab.tests.test_failure_validation \
  lab.tests.smoke_scenario_inputs.AdapterRateTests -q
```

Run the packet suite with ns-3 installed and without `NETLAB_SKIP_NS3=1`:

```sh
CXX=g++ python -m unittest lab.tests.smoke_scenario_inputs \
  lab.tests.smoke_experiments lab.tests.smoke_campaigns -v
```

Run original scripts in separate processes, serially:

```sh
export NETLAB_SKIP_NS3=1
for name in smoke_demo smoke_bgp smoke_stp smoke_fw smoke_mcast smoke_vrrp \
            smoke_lag smoke_survivability smoke_failures smoke_perf smoke_report \
            smoke_survivability_n2; do
  python -m "lab.tests.$name" || exit 1
done
```

These checks cover scenario admission for a valid project. They do not establish
complete validation of arbitrary malformed project documents, real emulation
behavior, or identical packet semantics across engines. A nanosecond-representable
rate may still require impractical CPU time; the format limits are not performance
guarantees. Existing Mininet ping clamps remain unchanged.
