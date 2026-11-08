# Result JSON integrity — 2026-11-08

Branch `fix/result-json-integrity`, based on `main` at `00168d4` (PR #38).
Behavior and limits: [result-integrity.md](../result-integrity.md).

| Check | Result |
| --- | --- |
| Complete backend + ML labeling + rate adapter | **477 tests passed**, 31.859 s |
| Original demo/model/API/report smoke scripts | **12 scripts passed**, `NETLAB_SKIP_NS3=1` |
| Actual IPv4/IPv6 ns-3 replication/campaign/study suite | **4 methods passed**, 170.209 s; **20 native runs** |
| Syntax and whitespace | `compileall`, `git diff --check` passed |

Nine new tests cover nested nonfinite constants/float overflow, filename identity,
exact uint64 counters, serialization and replace failure preserving prior bytes,
temporary-file cleanup, index removal/recovery after repair, huge timestamps,
queued bad-engine output, API history/retrieval, offline cohort arrays, and
authoritative committed JSON surviving a later index failure.

The native suite exercised six experiment/replay runs, eight campaign runs and
six parameter-study runs. Exact replay, paired effects and result reports passed
using the new atomic writer and strict reader. Sources remained frozen during
packet comparisons; documentation was completed afterward.

Reproduce the backend suite from the repository root:

```sh
python - <<'PY'
from pathlib import Path
import subprocess, sys
modules = ['lab.tests.' + p.stem for p in sorted(Path('lab/tests').glob('test_*.py'))]
subprocess.run([sys.executable, '-m', 'unittest', *modules, 'ml.test_labeling',
               'lab.tests.smoke_scenario_inputs.AdapterRateTests', '-q'], check=True)
PY
CXX=g++ python -m unittest lab.tests.smoke_experiments \
  lab.tests.smoke_campaigns lab.tests.smoke_studies -v
```

Native checks require ns-3 and `NETLAB_SKIP_NS3` unset. No privileged Mininet/FRR
test or power-loss durability experiment was performed. Atomic replacement does
not add fsync guarantees, and history signature-cache behavior remains unchanged.
