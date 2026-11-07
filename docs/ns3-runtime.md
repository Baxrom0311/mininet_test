# ns-3 process ownership

The API queue and the older standalone `RunManager` share
`lab/engines/ns3_process.py`. One run owns its input/result directory, the direct
subprocess, its separate process group, the stdout pipe and a readiness selector.
ns-3 still runs in a separate Python process; simulation models and results are
unchanged by the ownership helper.

The Linux selector reads the pipe on the same worker that handles progress/log
callbacks. There is no reader thread or cross-thread output queue. This removes
the dead-reader hang and applies pipe backpressure when a callback is slow.
Reads are at most 16 KiB; each stdout line is capped at 64 KiB. Oversized lines
are recorded as truncated diagnostics and discarded through their newline,
then processing resumes. The diagnostic tail contains at most six entries of
2048 characters. These limits apply to stdout diagnostics, not result series.

Normal JSON objects deliver numeric finite `progress` in 0..1 and string `log`
messages. Native text, scalar/list JSON and invalid UTF-8 diagnostics do not
crash a reader: nonobject lines become bounded diagnostics and decoding uses
replacement characters. Invalid progress/log fields fail the run. The result
file must contain a JSON object; nonfinite constants and overflowing floating
point values are rejected. The full result is loaded without truncation.

Queue cancellation is checked before spawning, between pipe polls (100 ms),
while processing individual lines, after stdout EOF and before loading results.
An executing callback must return before another check can run. A simulation
that closes stdout but stays alive has a 30-second exit timeout; cancellation
continues to work during that period. If the direct child has exited but a
descendant silently keeps stdout open, two seconds without readable data fails
the run. Time spent draining available output or inside callbacks does not count
as pipe inactivity. There is no wall-clock deadline for an otherwise running
simulation; queued users can cancel it.

On every normal/error return, cleanup sends TERM to the private group, waits up
to five seconds for the direct child, escalates to KILL if necessary and waits
again. A final group KILL targets remaining descendants, and the selector and
pipe are closed independently of signalling errors. The direct child is reaped;
descendants reparented after their parent exits are reaped by their new parent.
Descendants that deliberately create a different process group are outside this
ownership boundary. This helper does not manage privileged Mininet networks.

Callback, pipe, parser, nonzero-exit and result-file failures retain their original
exception. Cleanup failures are attached as exception notes; manager result JSON
persists them in `error_notes`. If cleanup fails after a valid result, the run
fails rather than reporting successful completion. Readiness-selector setup
failure also goes through subprocess cleanup.

Local subprocess regressions use ordinary Python scripts, including noisy output,
SIGTERM resistance and an inherited stdout descendant. They need neither root
nor an ns-3 installation. Actual IPv4/IPv6 packet experiments and campaigns are
separate validation:

```sh
python -m unittest lab.tests.test_ns3_process lab.tests.test_ns3_process_backlog lab.tests.test_ns3_lifecycle_integration -v
CXX=g++ python -m unittest lab.tests.smoke_experiments lab.tests.smoke_campaigns -v
```
