# Experiment input integrity — 2026-11-08

Branch `fix/experiment-input-integrity`, based on `main` at `aabc9e0` (PR #40).
Behavior, export format and limits: [experiment-input-integrity.md](../experiment-input-integrity.md).

| Check | Result |
| --- | --- |
| Complete backend + ML labeling + rate adapter | **497 tests passed**, 38.333 s |
| Both review branches together, complete backend + ML + rate adapter | **500 tests passed**, 42.097 s |
| Original demo/model/API/report smoke scripts | **12 scripts passed**, `NETLAB_SKIP_NS3=1` |
| Actual IPv4/IPv6 ns-3 replication/campaign/study suite | **4 methods passed**, 242.913 s; **20 native runs** |
| Syntax and whitespace | `compileall`, `git diff --check` passed |

Before the change, malformed replication counts, flow containers and manifests
raised `TypeError`/`AttributeError`, while a completed result with altered output
duration or scenario could still receive `complete=true`. Eleven new tests cover
controlled rejection, duplicate run identity, manifest/input consistency, finite
metric overflow, legacy optional metadata, incomplete runs, JSON/HTML/ZIP and
comparison endpoints, campaign diagnostics/raw exports, and recovery after repair.

The native suite exercised six experiment/replay runs, eight campaign runs and
six parameter-study runs, including both IP families. Packet comparisons ran with
this checkout's sources frozen; this validation document was added afterward.
The independent cancellation branch's backend suite ran during part of the native
suite, so the packet wall time above is not a standalone performance benchmark.

A targeted run encountered an existing campaign cancellation timeout. The complete
497-test run passed, but a deterministic follow-up reproduced a stale pending
transition overwriting a terminal worker commit. That correction and regression
are on the independent `fix/cancellation-terminal-race` branch (`a1b75e7`), also
based on PR #40. Review both branches to include that concurrency correction.
The combined check used a detached temporary checkout with both branches' changes
applied; they applied without conflicts. No integration merge was made on `main`.

```sh
python -m unittest lab.tests.test_experiment_input_integrity \
  lab.tests.test_experiments lab.tests.test_experiment_comparison \
  lab.tests.test_campaign_analysis lab.tests.test_campaigns -v
CXX=g++ python -m unittest lab.tests.smoke_experiments \
  lab.tests.smoke_campaigns lab.tests.smoke_studies -v
```

Native checks require ns-3 and `NETLAB_SKIP_NS3` unset. No privileged Mininet/FRR
test was performed. Hash consistency is not archive authentication; the new
checks cover analysis inputs rather than every possible result export field.
