# Topology planning and generation

`POST /api/topogen/plan` validates a request and returns exact router, switch,
host, device, link, transit-link and application counts before constructing any
graph objects. `POST /api/topogen` accepts the same options and returns the
project. Neither endpoint saves a draft, starts a network, or queues traffic.

```json
{"kind":"ring","routers":20,"hosts":5,"flows":40,
 "core_bw":1000,"lan_bw":100,"ipv6":true,"name":"dual_ring"}
```

The preview has `kind`, `ipv6`, normalized `options`, `counts`, and `limits`.
`counts.flows` is the number of applications **per scenario**. IPv6 adds an
alternative scenario rather than doubling concurrent traffic. Counts are format
and construction bounds, not RAM estimates or host capacity measurements.

## Options and limits

| Kind | Dimensions |
| --- | --- |
| `ring` | `routers >= 3`; ring plus the existing diagonal links |
| `star` | `branches >= 1`; HQ router plus one router/LAN per branch |
| `tree` | `depth >= 1`; binary router tree with LANs at its leaves |
| `campus` | `core`, `dist`, `access >= 1`; core mesh, distribution, access LANs |

All kinds accept `hosts` per LAN, `flows`, `name`, `core_bw`, `lan_bw`, and `ipv6`.
Campus also accepts `dist_bw`. Unsupported kinds/options, including dimensions
for a different kind, return HTTP 400 instead of a Python exception. The API
preserves known null/blank options as omitted form values. Direct Python calls
validate explicit values. Counts accept exact integers, integral floats and
integer strings, but reject booleans, fractions, NaN, infinity and overflow.

Budgets are checked before allocating a builder, looping over dimensions, or
raising two to a requested tree depth:

- At most **3000 devices** and **16000 router-to-router links**.
- At most **255 ports per device**, including star HQ/core/distribution fan-out.
- **0–245 hosts per LAN**. IPv4 hosts start at `.10`; `.254` is the last usable
  host address in the generated /24. The broadcast address is never assigned.
- **0–10000 applications**. Positive `flows` requires at least two hosts. Use
  `hosts=0, flows=0` for a router/switch topology without traffic.
- Finite bandwidths **0.000001–10000 Mbps** (at least one native bit/s).
- Project names use **1–40 characters** from `[A-Za-z0-9_-]`.

Tree depth is bounded before exponentiation and then subject to the device
budget. Preview counts account for every generated LAN switch, host, router
uplink and ring diagonal. Campus limits include the first two cores' distribution
links and each distribution router's access LANs.

`ipv6` must be a JSON boolean. Both HTTP endpoints now pass it through; generated
projects have IPv6 interface/gateway addresses and the existing `asosiy_ipv6`
scenario when flows are requested. The API also forwards the bandwidth options.

## Python and CLI

```python
from lab.topogen import generate, plan
preview = plan("campus", core=2, dist=4, access=8, hosts=5, flows=40, ipv6=True)
project = generate(preview["kind"], ipv6=preview["ipv6"], **preview["options"])
```

The direct `ring`, `star`, `tree`, and `campus` functions use the same preflight.
Their valid output retains the existing layout, identifiers, flow selection and
FRR startup configuration. Bounds are independent of available RAM/CPU; adaptive
run workers still decide how admitted simulations execute.

```sh
python -m lab.topogen ring --routers 20 --hosts 5 --flows 40 --ipv6 > /tmp/dual.json
```

Invalid CLI options report an argument error on stderr and produce no partial
JSON. Bandwidth controls are available through Python/HTTP; the CLI retains its
existing dimension/name/IPv6 flags.

## Verification

```sh
python -m unittest lab.tests.test_topogen lab.tests.test_generation_api \
  lab.tests.test_traffic_validation -v
python -m unittest lab.tests.smoke_generated_imports -v
```

The packet smoke generates all four kinds through the API, imports CSV for IPv4
and gravity demand for IPv6, and runs actual ns-3 traffic. It does not measure
privileged Mininet/FRR capacity. Native cases skip only when ns-3 is unavailable
or `NETLAB_SKIP_NS3=1` is set.
