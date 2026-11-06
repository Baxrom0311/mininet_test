# Real FRR scale benchmark evidence

Run on a dedicated root Linux host with Mininet, OVS, FRR, ping and iperf3:

```sh
sudo python3 -m lab.benchmarks.real_scale \
  --routers 10 20 40 --hosts 1 --flows 4 --duration 10 \
  --rss-budget-mb 2048 --output /tmp/scale.json
```

For a larger host, choose a budget and time allowance appropriate to that host.
For example, the following requests 50/75/100 routers with a 16 GiB sampled RSS
budget; these sizes have not been measured by this change:

```sh
sudo python3 -m lab.benchmarks.real_scale \
  --routers 50 75 100 --hosts 1 --flows 8 --duration 20 \
  --settle 180 --timeout 900 --rss-budget-mb 16384 --output /tmp/scale-large.json
```

Router counts must be unique, in 3..200. The benchmark holds the shared real
network lock for the entire invocation. Mininet cleanup is host-wide, so the
host must be dedicated to this test. Prerequisite checks can report `blocked`
before any network starts. Root alone does not establish that the kernel and
network tools will support a particular topology.

Each run checks OSPF Full adjacency counts, end-to-end ping, positive finite
throughput for **every configured flow**, and reachability after one core link
is disabled. The reachability probe is not a protocol convergence measurement:
the probed route may already use another link. Schema 2 marks its timing method
as `before_link_command_v2`; the timer includes the link command itself. Older
archived probe values used the previous method and are not rewritten.

Resource sampling is separate from the real test orchestration, in
`lab/benchmarks/scale_resources.py`. Daemonized FRR is discovered through network
namespace membership. Shared OVS RSS is reported separately and excluded from
the benchmark/namespace totals. Reads that race a normal process exit are
skipped. Permission failures, malformed counters, sampler startup failures and
discovery exceptions invalidate the resource evidence and prevent a passing
run. `resources.valid`, `sampling_error_count` and the first 20 `sampling_errors`
describe this distinction.

Only complete, identity-consistent process reads contribute counters. CPU
history uses PID plus process start time, retains identities across missing
samples and avoids counting a process's previous CPU usage again. Samples are
taken at entry, during the run, after network startup, before cleanup and at
exit. A short run therefore still includes an explicit live-network sample.
Periodic and explicit samples share a lock.

Sampling remains approximate: short-lived processes between samples may be
missed, RSS sums may count shared pages more than once, shared OVS belongs to the
host, and CPU/RSS are not exact kernel high-water marks. The RSS budget assesses
the completed measurement; it is not a hard memory limit during startup. A
sampler error invalidates a run even when its recorded RSS lies below budget.

Schema 2 reports retain the original top-level `status`, `runs`, `limits` and
`maximum_capacity_established` fields. They add an invocation `run_id`, settings,
planned router counts, `active_routers` and `stopped_early`. A complete JSON is
written before starting, before each size, after each run and at completion.
Writes use a temporary file and atomic replacement; a write failure stops the
invocation and preserves the previous checkpoint. This is process-interruption
protection, not a power-loss durability guarantee.

`driver.stop()` is attempted after startup, measurement and interruption errors.
If it raises, `cleanup_error` is retained alongside the primary error and resource
summary. A failed or interrupted run stops all later topology sizes. Ctrl+C and
SIGTERM request cleanup and persist interrupted metadata. Signal handlers are
restored on exit. `--timeout` raises during the run; cleanup runs after the alarm
is disabled. Cleanup is not bounded by that alarm. A hard kill, OOM kill or stuck
kernel operation can prevent cleanup and leave a `running` checkpoint with the
last `active_routers`; such a checkpoint is not a success result.

Exit codes: `0` all requested sizes passed, `1` a check/resource/budget/cleanup
failure, `2` blocked prerequisites or invalid arguments, `130` handled
interruption. A passing size or a budget failure never establishes the host's
maximum capacity: `maximum_capacity_established` remains false.

Existing [10/20/30-router measurements](validation/real-env-2026-11-06.md) remain
historical real evidence from their documented code version. The schema 2
orchestration needs a new privileged host run; synthetic regression tests are
not FRR/Mininet measurements.

```sh
python -m unittest lab.tests.test_scale_benchmark -v
```
