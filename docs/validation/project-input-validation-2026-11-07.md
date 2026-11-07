# Project input validation — 2026-11-07

Branch: `fix/project-input-validation`, based on `main` at `cd5d68c` (PR #33).
Contract: [project-validation.md](../project-validation.md).

## Verified behavior

- Malformed project containers and nested configuration return field errors
  before unsafe traversal, run admission, or network initialization.
- Numeric checks reject booleans, nonfinite values, overflow, fractional integer
  fields, and invalid protocol ranges. Independent BGP, STP and VRRP mistakes
  remain visible together, with the established feature names in their errors.
- Known optional nulls use adapter defaults on a detached execution copy. Saved
  drafts and opaque extension metadata are preserved. Three known private
  engine overrides are discarded at this boundary.
- Invalid live replacements and missing candidate targets leave the running
  document intact. Invalid link parameters or a nonboolean `up` value cause no
  model mutation or native link calls. Mocked real-driver checks cover this gate.
- Draft saving still returns HTTP 200 with warnings for unfinished configuration.
- Canonical interface/port names, IPv4 network/broadcast rules, native geometry
  bounds, and unique controller listening ports are checked before execution.

## Environment

Python 3.12, ns-3 `3.44.post0`, cppyy `3.5.0`, `CXX=g++`.
Observed cloud limits: **4 CPU quota / 32 GiB RAM**. These are not measurements
of the user's 64 GiB/i9/RTX3050 computer. `mn`, `vtysh`, and `iperf3` were absent;
no privileged Mininet/FRR or physical capacity test was performed.

## Results

| Check | Result |
| --- | --- |
| Complete backend modules + ML labeling + rate adapter | **372 tests passed**, 31.349 s |
| Original demo/model/API scripts, unchanged, `NETLAB_SKIP_NS3=1` | **12 scripts passed** |
| Final actual ns-3 packet suite | **7 methods passed**, 290.383 s; **24 native runs** across IPv4/IPv6 |
| Compatibility | All **17 example projects / 22 scenarios** remain valid; ring/campus/tree fixtures also pass |
| Syntax, lazy imports and whitespace | `compileall`, root simulation + real-driver imports, `git diff --check` passed |

The backend suite includes 52 new tests: 14 project/API tests, 14 scalar/domain
tests, 16 preparation tests, and eight live-driver guard tests. API tests include
16 invalid submissions across runs/studies/experiments/campaigns with no engine
calls or admitted work, rejected network starts, atomic live rejections, and
draft saving with warnings. Preparation tests exercise original-data immutability,
optional defaults, reserved runtime fields, /31 and /32 addresses, long interface
digits, canonical spellings, and validation order before IPv6 scenario traversal.

The packet suite comprises:

- Two new nullable-project runs, one per address family, with actual delivered
  UDP traffic and prepared-project metadata assertions.
- Two scenario-boundary runs: small payloads, rate spellings, large finite stop
  times, long echo intervals, and a completed FTP transfer with an automatic ID.
- Six experiment runs, including exact flow replay.
- Eight campaign runs, including paired bandwidth effects and compact reports.
- Six parameter-study runs: IPv4 two-dimensional bandwidth/rate variation and
  IPv6 one-dimensional rate variation.

One additional method is the native rate-format unit check. The final packet
suite ran against one frozen source tree; this evidence document was added
afterward.

## Reproduce

From the repository root, using the environment containing backend dependencies:

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
CXX=g++ python -m unittest lab.tests.smoke_project_inputs \
  lab.tests.smoke_scenario_inputs lab.tests.smoke_experiments \
  lab.tests.smoke_campaigns lab.tests.smoke_studies -v
```

Run the original scripts in separate processes, serially:

```sh
export NETLAB_SKIP_NS3=1
for name in smoke_demo smoke_bgp smoke_stp smoke_fw smoke_mcast smoke_vrrp \
            smoke_lag smoke_survivability smoke_failures smoke_perf smoke_report \
            smoke_survivability_n2; do
  python -m "lab.tests.$name" || exit 1
done
```

The rejection guarantees apply to input validation. Valid requests that later
encounter a physical network or daemon failure do not have transactional rollback.
No complete semantic guarantee is made for opaque extension metadata, custom
startup programs, or untested privileged emulation.
