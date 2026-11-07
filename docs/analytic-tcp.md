# Analytical TCP allocation and finite transfers

The flow engine prepares paths, per-flow rate/Mathis limits, Wi-Fi speed weights
and resource costs. `lab/engines/tcp_fluid.py` now handles weighted max-min
allocation and in-bin finite-transfer completion. It receives the capacity left
after UDP/ping traffic, including shared airtime costs.

Previously the allocation loop stopped after 64 filling passes. With 100 flows
capped at 1..100 Mbps and enough path capacity, flows above 64 Mbps remained
underallocated: aggregate 4.384 Gbps instead of 5.05 Gbps. The new loop freezes
flows on the actual limiting resource or rate cap, making progress until every
flow has its allocation. Duplicate resource keys are combined, including a Wi-Fi
air pool used more than once along a path.

The finite-transfer loop previously stopped after 32 completion events within
one sample. A case with 64 files completed only 32 and lost the remaining part
of the interval's service. It also forced every event to last at least 1 µs;
in a high-rate 1/2-byte transfer case the 2-byte flow reported 1251 bytes.

The new integrator advances to the next completion or the end of the interval.
Every partial-interval event removes a completed transfer; there is no fixed
32-event cap and no artificial minimum time step. Delivered payload is bounded
by remaining file bytes. A completed file's share is reallocated to remaining
flows for the rest of the interval, including ongoing streams. Capacity and
payload are integrated over time to produce the existing bin-average fields.

The existing handshake/slow-start approximation is retained: a finite transfer
reserves its allocated share while its setup delay elapses, and payload begins
after setup. This is not a real TCP congestion-control implementation. Mathis
caps, UDP-first residual capacity and weighted Wi-Fi fairness remain the same
model choices. Output throughput rounding still limits how precisely display
series can reconstruct bytes; the solver itself caps unrounded payload.

There is no universal wall-time guarantee: many distinct caps/completions can
require more work than the old truncated result. The solver's progress follows
the actual number of flows rather than silently stopping at a small constant.
Subsecond sampling is a separate feature branch; this fix works with the current
default one-second analytical engine and does not require that branch.

Run the deterministic public-engine benchmark without root or ns-3:

```sh
python -m lab.benchmarks.analytic_tcp --flows 1000 --output /tmp/analytic-tcp.json
```

It checks 1000 different finite file sizes and 1000 distinct rate-cap levels on a
three-router synthetic ring. Capacities are deliberately chosen to make the
requested files/rates feasible. The nominal high rate is an input to a fluid
model, not measured hardware throughput. JSON records requested/measured totals,
per-flow match counts, completion counts and wall time. CLI bounds are 1..2000
flows; exit 0 means both cases passed, 1 a failed check, 2 invalid arguments.
The [saved 1000-flow result](validation/analytic-tcp-benchmark-2026-11-07.json)
is explicitly labeled `synthetic_fluid_model`, `real_network_measurement=false`.

```sh
python -m unittest lab.tests.test_tcp_fluid lab.tests.test_analytic_tcp lab.tests.test_analytic_tcp_benchmark -v
```
