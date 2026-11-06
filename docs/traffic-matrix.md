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

Limits: CSV 2 MiB / 10000 flows; gravity 2..100 selected host/server endpoints;
per-flow rate 0.001 bps..10 Gbps. Very disproportionate weights producing a smaller
rate are rejected. These are input bounds, not promises of real emulator capacity.
`ip_version: 6` requires IPv6 addresses on endpoints and is supported by analytic
and ns-3. The current UI is unchanged; use API/JSON import.

Validation: `python -m unittest lab.tests.test_traffic -v` includes 1000 CSV flows,
functional analytic delivery, gravity conservation and API errors. The local
1000-flow, 2-second scenario completed in 0.079 s including import. This does not
benchmark 1000 real namespaces or arbitrary large topologies.
