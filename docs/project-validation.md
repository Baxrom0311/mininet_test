# Project validation and execution copies

`lab.model.validate_project(project)` returns field errors without changing the
caller's document. Known JSON containers are checked in `lab/project_shape.py`,
numeric fields in `lab/project_values.py`, and existing protocol validators run
after those checks. Invalid shapes cannot reach parsers that expect dictionaries,
lists, hashable device names, or numeric fields.

## Admission and editing

- `/api/validate` and `/api/scenario/validate` return an `errors` list. Project
  errors are returned before scenario parsing.
- Runs, parameter studies, experiments, and campaigns reject invalid projects
  before engine execution or run admission.
- Network start rejects an invalid project before initialization. Live project
  replacements validate a detached candidate before replacing the running
  document; named targets must exist in that candidate.
- Live link updates validate **both** parameters and the boolean `up` flag before
  changing model state or invoking native link configuration.
- Saving an unfinished draft retains the existing behavior: HTTP 200 with
  `warnings`. Validation errors do not prevent saving work in progress.

These guards cover input rejection. They do not provide rollback if a valid
request later encounters a physical network or daemon failure.

## Format rules

Projects require a string `name` and object-shaped `devices` and `links`.
Device records, configurations, link endpoints, controller records, and known
nested service/protocol containers have explicit types. Routes, VRRP entries,
firewall rules, DHCP pools, and BGP neighbors must be arrays of objects. Optional
null containers mean absent values; entries within a container cannot be null.
Unknown extension fields remain opaque and are preserved.

Device IDs and link/controller ports remain JSON integers; booleans and floats
are rejected. Configuration integer fields accept exact integral floats and
integer strings consumable by downstream `int()` calls, without truncating
fractions. Existing VLAN, RIP, BGP, STP, VRRP, bond, and firewall ranges apply.
DHCP pools require IPv4 addresses and positive uint32 lease seconds.

Device/controller/program names must match their entire allowed spelling,
including rejection of trailing newlines. Switch port keys are canonical decimal
strings (`"1"`, not `"01"`). Interface names use the device's canonical
`<name>-eth<port>[.<vlan>]` spelling, with ports 1–255, VLANs 1–4094 and Linux's
15-character limit; `lo` remains allowed. Interface length is checked before
converting digits, preventing oversized port spellings from raising exceptions.
Two local controllers cannot reserve the same listening port.

IPv4 network/broadcast addresses are rejected as interface addresses for prefixes
shorter than /31. Both addresses on /31 links and individual /32 addresses remain
valid. This does not prohibit ordinary IPv6 subnet addresses.

Link parameters use the same validation on admission and live updates:

| Field | Accepted values |
| --- | --- |
| `bw` | Finite 0.000001–10000 Mbps, at least one native bit/s |
| `loss` | Finite 0–100 percent |
| `queue` | Exact integer, 1–100000 packets |
| `delay`, `jitter` | Nonnegative finite `us`, `ms`, or `s`; plain numbers mean milliseconds |
| `medium` | `wire` or `wifi` |
| `qdisc` | `fifo`, `prio`, or `fq_codel` |

Link delays leave room for a 3600-second scenario inside signed 64-bit nanosecond
time. Timed scenario events use their actual scenario horizon for this check.
Defaulted null/empty link values retain their existing absent-value behavior.

Device coordinates must be JSON numbers or absent/null defaults, with finite
values bounded for interpolation, squared distances and native propagation time.
The bound shares `validation_numbers.MAX_POSITION` with move events and accounts
for `meters_per_px`. Negative/zero finite scales retain the radio helper's existing
minimum-scale fallback. Wi-Fi power is finite and within ±3000 dBm to prevent
exponent overflow; this broad numeric bound is not a physical radio-power policy.
Existing fallback behavior for unsupported finite widths/channels is preserved.

## Execution copies

`project_for_execution()` operates on a validated detached copy. It removes
known optional null values and null/blank link fields, letting adapter defaults
work consistently. It preserves healthy configuration and opaque extensions.
Missing/null device configuration becomes an empty object. It does not rewrite
saved drafts or the caller's input.

Three private runtime overrides (`_stp_hold`, `_lag_hold`, `_vrrp_master`) are
removed from submitted projects. Engines construct their own runtime state later.
Run reproducibility captures the prepared execution copy, including these
absent-value conventions.

## Checks

```sh
python -m unittest lab.tests.test_project_validation lab.tests.test_project_values \
  lab.tests.test_project_preparation lab.tests.test_live_project_guards -v
python -m unittest lab.tests.smoke_project_inputs -v
```

The packet smoke runs nullable project defaults through actual IPv4/IPv6 ns-3
traffic. It skips native execution only when ns-3 is unavailable or
`NETLAB_SKIP_NS3=1` is set. Mocked real-driver tests verify rejected inputs avoid
native calls; they do not replace privileged Mininet/FRR validation.
