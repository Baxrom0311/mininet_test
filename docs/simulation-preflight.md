# Simulation preflight

`POST /api/scenario/preflight` accepts the same `project`, scenario name and
`engine` fields as `/api/runs`. It returns a diagnostic report without creating
project result directories, admitting work, starting a network, or importing ns-3.

The report separates `valid` input, engine `available` status, `compatible` input
and `ready` execution. Unavailable engines include `unavailable_reason`. The
report uses HTTP 200 for diagnosed input errors; starting an invalid run still
returns HTTP 400. Authentication and origin checks remain unchanged.

`issues` have stable codes, severity, and messages. Grouped compatibility issues
include a total `count` and at most ten example `paths`. `counts` describes devices,
links, user and expanded applications, and expanded events. These are counts, not
resource-capacity predictions. `effective` includes address family, sample interval,
and ns-3's requested/environment/effective fast mode and MTU.

Known ns-3 blockers (VLAN trunks/subinterfaces and connected documentation-prefix
IPv6 addresses) are checked before normal/study/experiment/campaign admission.
The child also checks them before native imports. Empty topologies can execute
without failing the node-statistics reservation.

Advisories describe existing behavior: NAT is not translated, DHCP is statically
resolved, firewall blocking uses the initial model, routing protocols can use
model-derived static routes, P4/SDN programs are not executed, and jitter is ignored.
IPv4-only settings removed by IPv6 family selection are identified. Warnings do
not block runs or certify exact equivalence with live FRR/OVS.

`ns3_fast` must be a JSON boolean. `NETLAB_NS3_FAST=1` also requests fast mode;
Wi-Fi disables it. The report and runner share the option calculation. Fast mode
uses MTU 9000 and changes packet-level accuracy. Analytic reports identify the flow
approximation and sample-grid event timing.

This is a bounded inspection of known capabilities, not a complete proof of
reachability, all native numeric combinations, custom-program correctness, or
physical host capacity. Availability is a snapshot and may change before start.

```sh
python -m unittest lab.tests.test_simulation_preflight -v
python -m unittest lab.tests.smoke_preflight -v
```
