# Durable run queue

The API server uses `QueuedRunManager`. `NETLAB_RUN_WORKERS` selects 1..16 workers
(default 2), shared by normal runs and study variants. At most 256 runs may wait
or execute in this manager. Mininet execution is additionally serialized; existing
real-driver checks can reject concurrent real submissions. Pending work is not a
promise that a large emulation fits available memory.

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
