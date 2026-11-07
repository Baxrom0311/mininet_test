# Durable parameter studies

`POST /api/studies` prepares and validates every 1D/2D variant before admitting
work. Modified app scenarios are attached to their project snapshots before
validation, so execution, saved inputs and labels refer to the same variant.
An invalid later variant cannot leave earlier variants running. Axes contain
2..25 finite scalar values; booleans, malformed parameter shapes and nonfinite
values are rejected. The existing maximum is 49 combinations. Packet-size sweeps
require positive integers; other fields retain their existing parameter operators,
normalization and project/scenario validators.

The API's `QueuedRunManager` saves all planned variant rows in one SQLite
transaction, then schedules them behind the admission lock. Closed/full queues
reject synchronously; they no longer return a study ID with no durable rows.
The shared 256 pending/running limit counts every admitted study variant.
Storage failure rolls back admission. A synchronous scheduling failure cancels
the admitted batch and keeps diagnostic terminal rows. A later worker/dispatch
failure affects that run's state rather than erasing it.

The existing response fields remain and `run_ids` is additive:

```json
{"study_id":"...","runs":4,"run_ids":["...-v01","...-v02","...-v03","...-v04"]}
```

Rows/results retain `study_index`, optional `study_index2`, parameters, values and
labels. New `study_position` records the flat admission index and `study_total`
records the planned count. Every pending variant can be queried or cancelled as
a normal run as soon as the request succeeds. Input projects are not mutated.

Dispatcher groups enforce per-study concurrency without occupying waiting worker
threads. Analytic and Mininet studies admit one variant at a time; ns-3 respects
`NETLAB_NS3_PARALLEL` and the global worker ceiling. Eligible unrelated work can
use remaining workers. Auto mode also applies CPU/RAM and engine limits. Fixed
mode keeps its configured global concurrency, with group limits and the single
live Mininet network restriction. The older standalone `RunManager` shares
preparation/validation and response fields, but retains its producer execution
and has no durable admission guarantee.

| Endpoint | Behavior |
| --- | --- |
| `GET /api/studies/{project}/{study_id}` | Canonical durable states and completeness |
| `POST /api/studies/{project}/{study_id}/cancel` | Best-effort cancellation of all nonterminal variants |
| `GET /api/studies/{project}/{study_id}/report.html` | Standalone report, including pending/failed/cancelled variants |

Status reports planned/observed counts, progress, per-run states, missing/invalid
IDs and provably local orphan IDs. `complete=true` requires every known planned
variant to be present and `done`. `settled` requires a complete plan with terminal
states, including error/cancellation; `observed_settled` concerns only rows that
were found. Cancel responses retain per-variant `not_cancelled` reasons and
continue after a variant fails to cancel. Foreign live-owner rows are never
assumed dead or forcibly cancelled by another manager.

Abrupt process exit is handled by the existing run-store recovery: every planned
nonterminal row becomes `interrupted` when its owner is proven dead. Nothing is
replayed automatically. Local ownership evidence can diagnose lost workers after
a metadata failure without modifying their stored states. The one-server-per-
results-directory rule and PID-reuse recovery limitation still apply.

Historical rows lack a reliable planned count. They remain visible with
`planning=legacy`, unknown `planned_runs`/progress and `complete=false`; observed
successes cannot establish that all original variants finished. Deleting every
row removes the study from lookup; deleting some rows leaves a detectable gap.

Reports show all observed statuses and escaped errors. Charts use successful
metrics only and retain failed/pending axis values as holes. Missing or invalid
plan metadata prevents a success claim; corrupt axis metadata suppresses charts.
A pending-only study has a report rather than returning 404. Existing study
start/progress/done event keys remain; `done` means execution settled, and the
aggregate `status` distinguishes success, failure, cancellation and incomplete
metadata. Notification failure does not replace saved results. A study completion
notification cannot precede completion of its whole submission phase.

```sh
python -m unittest lab.tests.test_study_preparation lab.tests.test_study_queue lab.tests.test_study_reports lab.tests.test_study_notifications lab.tests.test_dispatch_groups -v
CXX=g++ python -m unittest lab.tests.smoke_studies -v
```

The new endpoints use the existing authentication/origin checks. No frontend editor
changes or privileged Mininet acceptance results are included.
