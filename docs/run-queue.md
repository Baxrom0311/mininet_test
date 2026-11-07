# Durable run queue

The API server uses `QueuedRunManager`. The default `NETLAB_RUN_WORKERS=auto`
adapts admission to effective CPU capacity, CPU load and available RAM, shared by
normal runs, study variants, experiments and campaigns. A numeric value selects
1..64 fixed workers. See [adaptive-workers.md](adaptive-workers.md) for resource
reserves, cgroup v2 limits, manual overrides and `/api/resources` diagnostics.
At most 256 runs may wait or execute in this manager. Mininet execution is additionally serialized; existing
real-driver checks can reject concurrent real submissions. Pending work is not a
promise that a large emulation fits available memory.

Parameter studies now reserve and persist their entire variant set before any
engine starts. Rejected invalid/full/closed submissions return synchronously.
Per-study concurrency uses dispatcher groups, and queued variants can be cancelled
immediately. [parameter-studies.md](parameter-studies.md) documents durable status,
group cancellation, report completeness and legacy compatibility.

Metadata lives in ignored `lab/results/runs.sqlite3` (WAL); series remain in the
existing per-project JSON files. States: queued, running, cancelling, done, error,
cancelled, interrupted. On startup, nonterminal records owned by a dead process
become interrupted. They are not replayed automatically. A live owner is not
assumed dead, including when its PID cannot be inspected. Use one server per
results directory. PID reuse is a remaining recovery limitation.

`POST /api/runs/{project}/{run_id}/cancel` cancels queued jobs immediately and
requests cooperative cancellation of active jobs. Analytic runs check at progress
callbacks. ns-3 cancellation also works during silent child startup, then terminates
and waits for the subprocess. Emulation cleanup terminates tracked traffic child
processes even when a callback fails. Actual privileged emulation cleanup still
needs validation on a real host.

`GET /api/runs/{project}/{run_id}` returns metadata while pending, and the result
when completed. To preserve existing clients, list responses retain `status=running`
for active rows and expose the actual pending phase as `queue_state`. Terminal
states are returned directly. Cancelling a single study variant does not cancel
the entire study. Active jobs must be cancelled before deletion.

Graceful server shutdown requests cancellation and joins its executor before
stopping the driver. Abrupt process death instead leaves recoverable interrupted
metadata. Tests cover worker caps, both cancellation paths, silent subprocess
cleanup and a real child-process registry/restart case.

History lists now use a persistent metadata/file-signature index, optional paginated
queries and filters. Legacy SQLite schemas migrate with a pre-upgrade backup;
unchanged result series are not parsed on each poll. See [result-index.md](result-index.md)
for compatibility, reconciliation, downgrade and benchmark details.

Normal, study and replicated submissions now share transactional admission with
campaigns. A batch's rows commit before scheduling, and workers wait until all
submissions finish. Scheduler failure cancels the admitted batch; queued cancellation
also releases its project payload while other workers remain busy. Unexpected
pre-result failures clear in-memory ownership, while committed JSON measurements
survive post-result callback/index failures. See [campaigns.md](campaigns.md).
