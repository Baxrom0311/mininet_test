# Traffic matrix import

`POST /api/traffic/import` receives a project and returns a validated `scenario`,
flow `count` and `offered_bps`. It does not save a project or start traffic.
Use the returned scenario in the project, then start it with `/api/runs`.

```json
{"project": {}, "mode": "csv", "duration": 30, "ip_version": 4,
 "csv": "src,dst,type,rate,start,stop\npc1,srv1,udp,5Mbps,0,30\n"}
```

Replace `{}` with the actual project. Required CSV headers are `src,dst`;
optional headers: `id,type,rate,size,start,stop,dscp`. Default type is UDP, which
requires a positive rate. Rates use `5Mbps`, `500kbps` or a plain number in Mbps.
TCP without a rate uses the engine's uncapped TCP model. Ping need not specify a
rate. Times must be finite and within the duration; IDs must be unique. Unknown
endpoints, malformed rows and unsupported columns fail the entire import.

Gravity import uses ordered demand `T_ij = total_bps*w_i*w_j / sum(w_a*w_b)` for
distinct endpoints. Positive weights can select a subset; omitted weights select
all hosts/servers uniformly. The result is deterministic and preserves total
offered demand. No self traffic is generated.

```json
{"project": {}, "mode": "gravity", "total_mbps": 100, "duration": 30,
 "weights": {"pc1": 1, "pc2": 2, "srv1": 4}}
```

Limits: CSV 2 MiB / 10000 flows / 131072 characters per field; gravity 2..100
selected host/server endpoints; per-flow rate 1 bps..10 Gbps. Very disproportionate
weights producing a smaller rate are rejected, without applying a hidden minimum
to each share. Total offered demand is preserved within decimal formatting
precision. These are input bounds, not promises of real emulator capacity.
`ip_version: 6` requires IPv6 addresses on endpoints and is supported by analytic
and ns-3. The current UI is unchanged; use API/JSON import.

CSV quoting uses the Python CSV parser's strict mode. Unclosed quoted fields,
trailing text after a closing quote, oversized fields, parser errors and numeric
overflow return validation errors; parser errors include a data-row and physical
line location. UTF-8 byte size and character/flow bounds are checked, and imports
do not change the process-global `csv.field_size_limit()`. An embedding application
with a lower global parser limit can reject a field earlier, with a controlled
error.

Payload sizes are exact integers in `1..65507`, and DSCP is an exact integer in
`0..63`. Integral CSV decimal/scientific spellings such as `128.0` and `4.6e1`
remain accepted and are converted exactly; fractional values are rejected before
float rounding could hide them. Boolean, nonfinite and overflowing numeric inputs
are rejected. Valid BOM-prefixed, CRLF and quoted records remain supported.

Validation uses a prepared project copy, including optional null defaults. Failed
rows never return a partial matrix or alter the submitted project. Native ns-3
rates use integer bit/s, so any remaining fractional bit/s are truncated there,
as for ordinary scenario applications.

Validation: `python -m unittest lab.tests.test_traffic -v` includes 1000 CSV flows,
functional analytic delivery, gravity conservation and API errors. The local
1000-flow, 2-second scenario completed in 0.079 s including import. This does not
benchmark 1000 real namespaces or arbitrary large topologies.

Boundary checks: `python -m unittest lab.tests.test_traffic_validation
lab.tests.test_generation_api -v`. Generated topologies can be previewed before
materialization; see [topology-generation.md](topology-generation.md).
