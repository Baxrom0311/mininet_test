# Cancellation must preserve a worker's terminal commit

A running worker can observe its cancellation token, write `cancelled` JSON and
commit terminal metadata while the cancellation request is still in progress.
Previously the request could then save its earlier `running` snapshot as
`cancelling`, overwriting the worker's terminal metadata. The unchanged result
signature meant ordinary reconciliation would skip the JSON: campaigns could
remain pending indefinitely even though the result file said `cancelled`.

The transition to `cancelling` now reads the current database row inside a
`BEGIN IMMEDIATE` transaction. It preserves `done`, `error`, `cancelled` and
`interrupted` rows. Otherwise it updates the current pending metadata, retaining
the latest label and result signature. It uses `(project, run_id)` throughout.
Worker commits and cancellation updates are serialized by SQLite without
holding the result-index lock across cancellation callbacks.

Normal cancellation still returns `cancelling` while a running worker is stopping,
and queued work can be cancelled immediately. The shared transition covers
individual runs, experiments cancelled per run, parameter studies and campaigns.
For an already affected historical row with a terminal JSON file, force reindex
with `POST /api/runs/{project}/reindex` to restore metadata from the result file.

The regression test forces the worker to commit its result before the request
can persist its pending transition. It fails on the original code and verifies
that the API-manager response, database, history and result JSON agree after
the correction. Separate tests cover every terminal/pending state, signatures,
latest metadata, project isolation and missing rows.

The serialization-error regression now waits for queue ownership (`futures`) to
be released before checking its final metadata. Engine progress (`active`) can
finish before the queue's exception handler has committed that error row; this
intermediate phase is not completion of the queued job.

```sh
python -m unittest lab.tests.test_cancellation_terminal_race \
  lab.tests.test_campaigns lab.tests.test_run_queue \
  lab.tests.test_study_queue lab.tests.test_study_notifications -v
```
