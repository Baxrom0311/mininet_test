# Cancellation terminal state — 2026-11-08

Branch `fix/cancellation-terminal-race`, based on `main` at `aabc9e0` (PR #40).
Behavior and recovery: [cancellation-terminal-state.md](../cancellation-terminal-state.md).

| Check | Result |
| --- | --- |
| Deterministic worker/cancel interleaving on unmodified main | Failed as expected: response/registry `cancelling`, result JSON `cancelled` |
| Focused cancellation, campaign, queue and study regressions | **35 tests passed**, 9.694 s |
| Complete backend + ML labeling + rate adapter | **489 tests passed**, 45.728 s |
| Syntax and whitespace | `compileall`, `git diff --check` passed |

The new integration regression runs a real queue worker with an in-process test
engine. A synchronization barrier forces its terminal result commit before the
cancellation request resumes its pending metadata transition. It verifies the
manager response, history, durable metadata and result file agree after cleanup.
Two additional tests cover every terminal/pending state, current labels, result
signatures, project isolation and missing rows.

The first complete-suite attempt exposed an existing serialization-error test
that awaited engine progress cleanup instead of queue completion. Its wait now
uses queue ownership; no timeout was increased. The final complete suite above
passed with that corrected completion criterion.

No Mininet/FRR or native ns-3 cancellation experiment was run on this branch.
The engine itself is unchanged; this validation covers queue/database ordering.
