# Simulation preflight — 2026-11-08

Branch: `feat/simulation-preflight`, based on `main` at `e5f763e` (PR #37).
Contract: [simulation-preflight.md](../simulation-preflight.md).

`POST /api/scenario/preflight` separates validated input, compatibility,
availability and readiness without creating a project result directory or
admitting work. Known blockers now reject runs and all study/replication/campaign
variants before native launch. Diagnostics group example paths and preserve
advisories instead of silently claiming native protocol equivalence.

The runner checks compatibility before importing ns-3. It shares effective fast
settings with the report and safely handles an absent FlowMonitor on empty/L2-only
topologies, including the debug statistics path. `ns3_fast` has a boolean contract.

| Check | Result |
| --- | --- |
| Full backend + ML labeling + rate adapter | **468 tests passed**, 29.434 s |
| Original demo/model/API smoke scripts, unchanged | **12 passed**, `NETLAB_SKIP_NS3=1` |
| Packet suite | **8 methods passed**, 317.562 s; **30 native runs** |
| Final shared-option/debug guards | Both native preflight methods passed again with `NETLAB_NS3_DEBUG=1`, 28.380 s |
| Final preflight API regression | **6 tests passed**, 0.512 s |
| Syntax and whitespace | `compileall` and `git diff --check` passed |

The six new API tests cover read-only behavior and immutability, independent
availability, VLAN rejection in four admission routes without creating runs,
family-specific documentation-prefix rejection, boolean/environment/Wi-Fi mode
selection, advisories, and malformed inputs. Native tests verify empty-project
execution and IPv6 fast-mode traffic against the report. Existing native tests
add eight generated/imported runs, six experiment/replay runs, eight campaign
runs and six parameter-study runs.

The packet comparison suite ran with frozen sources. The final empty-monitor
debug guard and shared fast-option refactor were verified by the two targeted
native methods afterward. No privileged Mininet/FRR tests or host capacity
measurements were performed. This report does not establish safety of every
native numeric combination or correctness of custom programs.

Reproduce from the repository root:

```sh
python - <<'PY'
from pathlib import Path
import subprocess, sys
modules = ['lab.tests.' + p.stem for p in sorted(Path('lab/tests').glob('test_*.py'))]
subprocess.run([sys.executable, '-m', 'unittest', *modules, 'ml.test_labeling',
               'lab.tests.smoke_scenario_inputs.AdapterRateTests', '-q'], check=True)
PY
CXX=g++ python -m unittest lab.tests.smoke_preflight lab.tests.smoke_generated_imports \
  lab.tests.smoke_experiments lab.tests.smoke_campaigns lab.tests.smoke_studies -v
CXX=g++ NETLAB_NS3_DEBUG=1 python -m unittest lab.tests.smoke_preflight -v
```

Run original smoke scripts serially in separate processes with
`NETLAB_SKIP_NS3=1`, as described in the previous validation report. The native
suite requires the ns-3 wheel; setting `NETLAB_SKIP_NS3=1` skips native cases.
