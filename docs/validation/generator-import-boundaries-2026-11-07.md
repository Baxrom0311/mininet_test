# Generator and traffic-import boundaries — 2026-11-07

Branch: `fix/generator-import-boundaries`, based on `main` at `b2bddef` (PR #36).
Contracts: [topology generation](../topology-generation.md) and
[traffic import](../traffic-matrix.md).

## Changes verified

- Preview and generation share per-kind option validation and exact graph counts.
  Device, transit-link, port, flow and IPv4 LAN limits are checked before graph
  allocation. Tree depth is bounded before exponentiation. Direct builders and
  CLI calls have the same guards.
- `/api/topogen` now forwards IPv6 and bandwidth settings. The new
  `/api/topogen/plan` endpoint returns normalized options, exact counts and limits
  without saving or starting anything. Known null/blank form defaults remain.
- Strict CSV syntax, byte/field/flow bounds, exact integer conversion and finite
  numeric checks produce controlled validation errors. The parser's process-global
  field limit is never changed. Import failures do not return partial matrices.
- Gravity demand remains deterministic and conserves offered demand within
  decimal formatting precision. Shares below one native bit/s are rejected
  rather than floored. Requested projects and opaque extensions stay unchanged.

For example, a 20-router ring with five hosts per LAN and 40 flows previews as
20 routers, 20 switches, 100 hosts, **140 devices / 144 links**, including
24 router-to-router links. Flow counts apply per alternative scenario, including
the IPv6 scenario; they do not imply concurrent execution of both.

## Results

| Check | Result |
| --- | --- |
| All backend modules + ML labeling + native rate adapter | **462 tests passed**, 34.678 s |
| Original demo/model/API smoke scripts, unchanged | **12 scripts passed**, `NETLAB_SKIP_NS3=1` |
| Actual ns-3 packet suite | **6 methods passed**, 390.952 s; **28 native runs** across IPv4/IPv6 |
| Compatibility review | 18 valid IPv4/IPv6 generation profiles retained byte-for-byte JSON output against the base generator |
| Syntax, lazy imports and whitespace | `compileall`, root simulation + real-driver imports, `git diff --check` passed |

There are 38 new unit/integration checks: 14 topology tests, 17 traffic-boundary
tests and seven API tests. Builder mocks establish that rejected dimensions and
previews do not construct a graph. Tests cover exact count budgets, shared-router
port fan-out, /24 broadcast avoidance, giant inputs, wrong-kind dimensions,
CLI errors, strict CSV quoting, parser limits without global mutation, exact
decimal integer spellings, gravity conservation and caller-data immutability.

API checks include 40 malformed topology requests and 14 resource-budget requests
across the two topology routes before builder allocation, plus 17 rejected traffic
imports with no queued work. Valid BOM/quoted CSV and generated dual-stack projects
also execute successfully in the analytic engine.

The native suite ran eight new end-to-end simulations: each of ring, star, tree
and campus was generated through the API, preview counts were checked, CSV demand
was imported for IPv4 and gravity demand for IPv6, then every imported flow had
measured positive throughput above 200 kbit/s. Existing packet checks added six
experiment/replay runs, eight campaign runs, and six parameter-study runs. Exact
replay, paired bandwidth effects, and study reports passed.

Packet comparisons ran against one frozen source tree. This evidence document
was added afterward.

## Reproduce

From the repository root, with the backend dependencies installed:

```sh
python - <<'PY'
from pathlib import Path
import subprocess, sys
modules = ['lab.tests.' + p.stem for p in sorted(Path('lab/tests').glob('test_*.py'))]
subprocess.run([sys.executable, '-m', 'unittest', *modules, 'ml.test_labeling',
                'lab.tests.smoke_scenario_inputs.AdapterRateTests', '-q'], check=True)
PY
```

With ns-3 installed and `NETLAB_SKIP_NS3` unset:

```sh
CXX=g++ python -m unittest lab.tests.smoke_generated_imports \
  lab.tests.smoke_experiments lab.tests.smoke_campaigns lab.tests.smoke_studies -v
```

Original scripts run serially in separate processes:

```sh
export NETLAB_SKIP_NS3=1
for name in smoke_demo smoke_bgp smoke_stp smoke_fw smoke_mcast smoke_vrrp \
            smoke_lag smoke_survivability smoke_failures smoke_perf smoke_report \
            smoke_survivability_n2; do
  python -m "lab.tests.$name" || exit 1
done
```

The observed cloud limits were **4 CPU quota / 32 GiB RAM**, with Python 3.12,
ns-3 `3.44.post0`, cppyy `3.5.0`, and `CXX=g++`. No privileged Mininet/FRR test
was run. These format/construction limits do not measure the user's
64 GiB/i9/RTX3050 host or predict how many real namespaces it can run.
