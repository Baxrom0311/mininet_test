# Adaptive run workers

The API queue defaults to `NETLAB_RUN_WORKERS=auto`. It samples resources every
second and admits work without occupying a worker thread for each waiting run.
Normal runs, studies, experiments and campaigns share this dispatcher. Pending
runs retain `queued` metadata and can be cancelled while resources are scarce.

The CPU ceiling uses process affinity and the tightest visible cgroup v2
`cpu.max`, including parent groups. It rounds fractional capacity down, with a
minimum of one, and reserves one logical CPU when capacity is four or more.
Admission starts at this ceiling (bounded by 64). At 95% measured CPU utilization
it decreases by one per sample; at 75% or less it increases by one. Intermediate
load retains the limit. Utilization is the larger of affinity CPU tick utilization
and quota-relative cgroup usage. Active jobs continue when the limit shrinks.

RAM uses `/proc/meminfo` and visible cgroup v2 `memory.max`/`memory.current`,
including parents. The dispatcher leaves 15% of effective total memory for the
OS/server, with a 128 MiB floor and an 8 GiB cap. Each admitted ns-3 or Mininet
run reserves an estimated 2048 MiB; an analytic run reserves 256 MiB. New work
must fit both observed available memory after the reserve and the total budget
after active estimated reservations. Admission credits are refreshed each second;
finishing a run can therefore take up to a second to make memory available for
the next one. With insufficient RAM, work waits until memory becomes available
or the user cancels it. Unknown available RAM limits concurrency to one. Sensor
exceptions also fall back to one and appear as `sensor_error`.

These are admission estimates, not enforced process memory limits. A single large
topology can exceed its estimate; increase `NETLAB_RUN_MEMORY_MB` for heavy runs.
CPU utilization changes after admission, so CPU pressure can also reduce the
number of new runs below the estimated ceiling. Physical idle threads in the
underlying pool may remain after concurrency decreases; active simulations and
their child processes follow the dispatcher limit.

| Setting | Default | Meaning |
| --- | --- | --- |
| `NETLAB_RUN_WORKERS` | `auto` | Adaptive dispatch, or a fixed integer 1..64 |
| `NETLAB_RUN_MAX_WORKERS` | `64` | Auto mode upper bound, 1..64 |
| `NETLAB_RUN_MEMORY_MB` | `2048` | Estimated MiB for each ns-3/Mininet run, 1..1048576 |
| `NETLAB_NS3_PARALLEL` | global ceiling | Optional study producer bound, 1..64, capped by the global ceiling |

Study producer threads submit runs and wait for their futures. They do not bypass
the global dispatcher. The older standalone `RunManager` estimates its study
parallelism once at study start; the API's `QueuedRunManager` adapts throughout
execution. Setting a fixed worker count preserves previous executor behavior and
bypasses adaptive CPU/RAM admission.

In auto mode at most one analytic run executes at a time because its Python
calculation shares the GIL, and at most one Mininet run executes against the
live network. Independent ns-3 subprocesses use the remaining capacity. A blocked
analytic/Mininet job does not prevent another eligible engine from running.
FIFO order is retained among jobs eligible under these restrictions.

On an otherwise idle 64 GiB machine, the default memory budget can accommodate
up to 28 estimated 2 GiB runs. Effective CPU capacity and measured available RAM
can lower that number. An i9's exact capacity is detected at runtime rather than
inferred from its product name. The current simulation engines do not use CUDA;
an RTX 3050 does not increase this CPU/RAM budget.

`GET /api/resources` uses the same authentication as other API endpoints. It
returns `mode`, `ceiling`, current admission `limit`, `active`, `queued`, active
engine counts, estimated reserved bytes, remaining admission credit, and the
effective CPU/RAM snapshot. In fixed mode it returns the configured limit and
current future counts along with a resource snapshot.

Linux `/proc` and cgroup **v2** are supported. Hidden limits and cgroup v1 limits
are not detected. Use explicit conservative worker/memory settings on hosts with
such configurations. The dispatcher applies to one server process, consistent
with the existing one-server-per-results-directory rule.

Run the regression suite with:

```sh
python -m unittest lab.tests.test_adaptive_workers -v
```
