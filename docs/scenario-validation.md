# Scenario input validation

All queued runs, experiment replications, and campaign variants use
`lab.model.validate_scenario` before they are admitted. Invalid scenario inputs
return HTTP 400 with field errors, before creating a run or starting an engine.
The public function still returns a list of errors and leaves caller data intact;
its implementation is in `lab/scenario_validation.py`.

## Input rules

| Field | Accepted values |
| --- | --- |
| `duration` | Finite number, 1–3600 seconds; numeric strings remain accepted |
| `sample` | JSON number, 0.001–3600 seconds, at most 100,000 intervals |
| `seed`, `rng_run` | JSON integer, 1–2147483647 |
| `apps`, `events` | Arrays of objects; omitted or null means an empty array |
| Application ID | String, unique including generated IDs; missing/null/empty IDs use `a1`, `a2`, etc. |
| `start`, `stop` | Finite numbers, `0 <= start < stop` and `start < duration`; default start is 1 second |
| `size` | Exact integer, 1–65507 bytes; multicast requires at least 20 bytes for ns-3's SeqTsSizeHeader |
| `bytes` | Optional exact integer, 1–18446744073709551615; zero no longer silently selects unlimited transfer |
| `dscp`, `dport` | Optional exact integers, respectively 0–63 and 1–65535 |
| Supplied application `rate` | At least 1 bit/s; UDP requires it, TCP can omit it for bulk transfer |
| Paced UDP/TCP/multicast rate | No more than `size * 8 * 1e9` bit/s, so packet serialization fits ns-3's 1 ns timer resolution |
| Ping `interval` | Finite number, 1e-9–9223372036 seconds |
| Event `t` | Finite number in `0..duration` |
| Move `x`, `y`, `duration` | Finite numbers; move duration is nonnegative |

Booleans do not count as numbers. Integer fields accept integer JSON values,
integral floats, or integer strings such as `"128"`; they reject fractions and
decimal strings such as `"128.0"` that downstream `int()` consumers cannot read.
NaN, infinity, and numeric overflow are rejected.
Move endpoints and their initial positions also leave room for interpolation and
squared distances and Wi-Fi propagation timers. Their absolute canvas coordinates
are bounded by the smaller of `sqrt(DBL_MAX) / 4` and
`(MAX_TIMER_S - 3600) * 3e8 / 4`, divided by `max(meters_per_px, 1)`.
This avoids overflow in intermediate positions or native propagation time; the
limit remains far above useful network dimensions.

`parse_rate` preserves numeric/unsuffixed Mbps and existing rate units. It checks
the finite unsigned-64-bit DataRate range; its generic parser can represent zero,
while application validation requires at least 1 bit/s. The native adapter sends
one canonical integer `bps` spelling to ns-3, avoiding unsupported exponent/unit
spellings. Fractional bit/s are truncated only at the native DataRate boundary.
These representability limits do not predict how much traffic a host can simulate.

HTTP `page` and FTP `file` must resolve to exact positive integral byte counts
within unsigned 64-bit range. Decimal unit syntax remains compatible: `1.5KB`
means 1500 bytes, and legacy `KiB`, `MiB`, `GiB` still use decimal multipliers.
HTTP think time must be finite and positive; codec and FTP direction must be known.

Multicast validates receiver names, IPv4 group range, rate, and packet size.
`dst` lists/comma-separated strings and the existing `receivers` alias are accepted.
Expanded multicast metadata is also checked before native address construction.
IPv6 multicast remains unsupported.

IDs are checked after high-level expansion as well: a VoIP ID `call` cannot
collide with an ordinary application's `call.up`, and duplicate multicast
receivers cannot create duplicate flow IDs. Expansion is checked one application
at a time; the maximum is 56036 expanded applications, keeping assigned native
ports within uint16 range. This is a format limit, not a scalability claim.

Finite application stops beyond the scenario end remain valid. The ns-3 adapter
clips them before constructing a native timer. Ping intervals longer than the
scenario are clipped in the adapter too; this cannot add an echo inside the
observed window. Event link delays/jitter must be finite and fit signed 64-bit
nanosecond time including the scenario horizon. Existing link range rules remain.

## Failure generation budget

Failure specifications require object-shaped groups, finite positive MTBF/MTTR,
known target names, and an exact integer failure seed (signed seeds remain valid).
Malformed events are rejected before failure generation sorts them.

`MAX_RAW_INTERVALS = 5000` bounds per-target schedules, the total across targets,
and device-to-link fan-out before interval union/cause attribution. A schedule
that exceeds the budget raises a validation error instead of silently truncating
or doing unbounded preprocessing. The existing 5000 generated-event limit still
applies after merging. Seeds and ordinary fixed/exponential schedules retain
their prior deterministic results.

## Verification

```sh
python -m unittest lab.tests.test_scenario_validation \
  lab.tests.test_application_validation lab.tests.test_failure_validation -v
python -m unittest lab.tests.smoke_scenario_inputs -v
```

The native smoke uses IPv4 and IPv6 packets to exercise one-byte payloads,
exponent-form numeric rates, alternate bit/s units, huge finite stop times,
long ping intervals, and automatic IDs for a finite FTP transfer. It skips native
cases only when ns-3 is unavailable or `NETLAB_SKIP_NS3=1` is set.

This validation assumes a valid project. It does not establish full structural
validation of arbitrary project documents, privileged Mininet behavior, physical
bandwidth capacity, or equality of packet semantics between engines. The existing
Mininet ping interval/payload clamps remain in effect.
