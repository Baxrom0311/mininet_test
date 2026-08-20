# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Mininet + SDN (os-ken/Ryu) network simulation for generating ML/AI training datasets (flow
records, DNS, HTTP, TCP states, NAT translations, anomalies/attacks). Most code comments, CLI
help text, and print output are in Uzbek.

The **active simulator** is a set of task-based Python modules at the repo root, orchestrated
by `light_simulation.py`. Two older, non-interoperating generations of the simulator have been
moved to `legacy/` and are not part of the active code path — see "The `legacy/` archive"
below. Know which generation you're touching before editing; if a request doesn't specify, the
active modules at repo root are almost always what's meant.

## Repo layout (root modules)

`light_simulation.py` is a thin (~270-line) CLI/`main()` orchestrator; all actual logic lives
in separate modules it imports. One-line responsibility of each:

| Module | Responsibility |
|---|---|
| `light_simulation.py` | CLI arg parsing + `main()` orchestration: builds the network, wires up traffic/impairments/collection, runs for `--duration`, tears down, builds the dataset. |
| `config.py` | Shared constants (`DATA_DIR`, `CONTROLLER_PORT`) that every other module imports. |
| `topologies.py` | `TOPOLOGIES` dict — declarative switch/host/link/AS definitions for the 4 topologies. |
| `routing.py` | `compute_paths()` and per-protocol path/cost logic for the 11 routing modes (Dijkstra, BFS, OSPF/IS-IS/EIGRP/RIP metrics, BGP path scoring, ECMP). |
| `network_build.py` | Builds the Mininet `Topo`/`Mininet` instance (`build_topology`), applies QoS/DiffServ `tc` qdiscs (`apply_qos_qdiscs`), and sets up/monitors the NAT gateway (`setup_nat_gateway`, `NATMonitor`). |
| `controller.py` | Starts the os-ken/Ryu SDN controller (`TransportMonitor` app) as a subprocess (`start_controller`) and manages its lifecycle. |
| `traffic_gen.py` | `TrafficGen` — diurnal-pattern traffic generation: HTTP, iperf3, DNS (multi-stage recursive resolution), anomalies/attacks, TCP congestion-control selection, QoS/DSCP marking, adaptive-bitrate video, connection-state tracking. |
| `impairments.py` | `Impairments` — injects the 10 dynamic network impairment types (congestion, link flap, reorder, buffer bloat, MTU blackhole, duplicate, jitter spike, etc.) onto live links during a run. |
| `collector.py` | `Collector` — runs `tcpdump` per switch interface and periodic ICMP RTT sampling between random host pairs. |
| `path_tracer.py` | `PathTracer` — for each host pair, records the theoretical path (via `routing.compute_paths`) alongside measured ping RTT/loss. |
| `dataset_builder.py` | `build_dataset()` — converts raw JSONL logs into the CSV/Parquet dataset files (see "Dataset output" below). |
| `visualize.py` | `visualize_topology()` — matplotlib/networkx PNG rendering of a topology + routing choice, runnable without root via `--visualize`. |
| `netutil.py` | Small shared utilities with no other-module dependency: `parse_ping()` (ping output → RTT/loss dict, used by both `collector.py` and `path_tracer.py`) and `graph_diameter()` (switch-graph BFS diameter, used by `light_simulation.py`'s STP-wait calculation). |

**Critical convention: lazy Mininet/os-ken imports.** Every module that needs `mininet.*` or
`os_ken`/`ryu` imports those packages *inside functions*, never at module top level (verified:
no top-level `import mininet`/`from mininet`/`import os_ken` anywhere in these root modules —
`controller.py`'s os-ken imports live inside a string template (`_CONTROLLER_APP_HEADER` +
`_CONTROLLER_APP_PATHS` + `_CONTROLLER_APP_BODY`, assembled by `_render_controller_app()`) that
is written out and run as a separate subprocess, not imported by the parent process). This lets every module be
imported and unit-tested on a machine without Mininet/OVS installed — e.g. this repo's dev
Mac. **Never move a Mininet/os-ken import to module top-level.** When adding code, follow the
same pattern: `import` inside the function that actually touches the network.

## The `legacy/` archive

`legacy/` holds two older, fully independent simulator generations that predate the current
module split. They are archived for reference only — **not run, not maintained, not part of
the active code path**:

- `legacy/main.py` + `legacy/ryu_transport_controller.py` — a minimal 9-switch Mininet lab with
  a standalone Ryu/os-ken controller process, plus a `--mode test` smoke test.
- `legacy/run_simulation.py` + `legacy/realistic_internet/` — an older v3 design built around an
  external **ONOS** controller in Docker, a fixed 5-AS/24-switch topology, and sFlow-RT-based
  collection. Requires Docker + the `onosproject/onos` image.
- `legacy/test_basic.py` — a throwaway 2-switch/2-host smoke test.
- `legacy/install.sh` — installer for the ONOS/v3 generation above.

If asked to add a topology, routing mode, traffic type, or anomaly, that work belongs in the
root modules (`topologies.py`, `routing.py`, `traffic_gen.py`, `impairments.py`) — not in
`legacy/`. Only touch `legacy/` if explicitly asked to.

## Commands

Simulation entry points that build a real Mininet network require root (`sudo`) because Mininet
manipulates network namespaces/OVS. `--dataset-only` and `--visualize` are pure-Python paths
that don't touch the network and don't need root.

```bash
# Install (bare Ubuntu 22.04+/24.04, no Docker)
sudo bash install_light.sh

# Run
sudo python3 light_simulation.py                                          # default: three_as + l2_learn, 180s
sudo python3 light_simulation.py --topology five_as --routing ospf --duration 300
sudo python3 light_simulation.py --topology datacenter --routing ecmp
sudo python3 light_simulation.py --topology campus --routing hybrid --duration 300
sudo python3 light_simulation.py --topology three_as --cli                # Mininet CLI, no auto traffic
python3 light_simulation.py --dataset-only                                # rebuild CSVs from a previous run's raw JSONL, no root/network needed
python3 light_simulation.py --topology five_as --routing spf --visualize  # PNG topology map, no root needed
sudo python3 light_simulation.py --no-traffic --no-impairments            # topology + routing only

# Docker
docker build -f docker/Dockerfile -t internet-sim .
docker run --privileged --rm -v $(pwd)/output:/data internet-sim --topology five_as --routing spf --duration 300
docker run --privileged --rm -it -v $(pwd)/output:/data internet-sim --topology campus --routing spf --cli
```

There is no pytest/unittest suite. On a machine without Mininet (e.g. this dev Mac), verify
pure-Python logic directly — e.g. `python3 -c "from routing import compute_paths; from
topologies import TOPOLOGIES; print(compute_paths(TOPOLOGIES['five_as'], 'ospf'))"` — and check
syntax with `python3 -c "import ast; ast.parse(open('X.py').read())"`. Do not attempt to start
Mininet/Docker on a machine that doesn't have them.

CI (GitHub Actions, `.github/workflows/ci.yml`) runs on every push/PR and covers exactly these
Mininet-free checks: `py_compile` over the root modules, `import light_simulation` (guards the
lazy-import convention — Mininet/os-ken are deliberately not installed, so a top-level
`import mininet`/`os_ken` breaks CI), a `compute_paths()` sanity sweep over all 4 topologies x
11 modes (non-empty, full distinct-host pair coverage, correct path endpoints — it does *not*
assert loop-free paths, since `routing.py` injects deliberate RIP/OSPF path imperfections), and
a `docker build` (build only) that then imports every copied module inside the image to catch a
missing Dockerfile `COPY` regression.

Cleanup between runs matters: Mininet/OVS state from a crashed run will break the next one.
`light_simulation.py` calls `mininet.clean.cleanup()` at start and in its signal handler/finally
block; if a run is interrupted outside the script (killed process, host reboot), run
`sudo mn -c` manually before retrying.

## The `docker/` folder

`docker/Dockerfile`, `docker/docker-entrypoint.sh`, and `docker/requirements.txt` live together
under `docker/`, but **the build context is still the repo root** (the Dockerfile `COPY`s
`light_simulation.py` and the other root modules directly, and `docker/requirements.txt` by
path) — hence `docker build -f docker/Dockerfile -t internet-sim .` (note the trailing `.`).
`.dockerignore` intentionally stays at the repo root for the same reason. The image is based on
`ubuntu:24.04`, installs Mininet/OVS/iperf3/tcpdump/hping3 via apt, installs
`docker/requirements.txt` via pip, and runs via `docker-entrypoint.sh` (starts OVS, cleans prior
Mininet state, applies sysctls, then `exec python3 /app/light_simulation.py "$@"`). `--privileged`
is required at `docker run` time for Mininet's namespace/OVS manipulation.

## Topologies (4)

Defined in `topologies.py`'s `TOPOLOGIES` dict; verified counts (`switches`/`hosts` dict sizes):

| Name | Switches | Hosts | AS numbers | NAT |
|---|---|---|---|---|
| `three_as` | 6 | 12 | 100, 200, 300 | yes (`192.168.60.0/24`) |
| `five_as` | 9 | 12 | 100, 200, 300, 400, 500 | yes (`192.168.50.0/24`) |
| `datacenter` | 6 | 8 | 100 (single AS, fat-tree) | no |
| `campus` | 7 | 11 | 100, 200 | yes (`192.168.70.0/24`) |

## Routing (11 modes, not 10)

`hybrid` was added this session — `compute_paths()` in `routing.py` now dispatches 11 modes,
not the 10 an older version of this doc described:

`l2_learn` (reactive MAC-learning flood — no precomputed paths), `rip` (hop count, max 15,
split horizon), `ospf` (link-state Dijkstra, area-based cost = ref_bw/interface_bw), `isis`
(link-state, wide metric, L1/L2 hierarchy), `eigrp` (composite BW+delay metric, DUAL-style),
`bgp` (AS-PATH, local-pref, MED, route dampening), `ecmp` (hash-based equal-cost multipath),
`spf` (generic Dijkstra shortest path), `policy` (AS-preference policy routing), `static`
(admin-defined routes), and `hybrid` (intra-AS OSPF as IGP + inter-AS BGP as EGP + ECMP within
that — the closest analogue to how the real internet actually routes).

## Features added this session

1. **TCP congestion control** (`traffic_gen.py`, `TCP_CC_ALGORITHMS = ["cubic", "reno", "bbr"]`)
   — each simulated host is assigned a CC algorithm via
   `sysctl -w net.ipv4.tcp_congestion_control=<cc>`; the choice is logged per flow (`tcp_cc`
   field) so the dataset captures which algorithm produced which throughput/RTT/loss pattern.
2. **DNS hierarchy** (`traffic_gen.py`, `TrafficGen._dns_loop`) — simulates real multi-stage
   recursive resolution (resolver → root → TLD → authoritative), each stage independently
   cached with its own TTL (root ~6h, TLD ~1h, authoritative per-domain TTL), logging
   cache-hit vs. each resolution stage separately to `dns_queries.jsonl`.
3. **QoS/DiffServ** (`network_build.py`, `_setup_qos_qdisc` + `traffic_gen.py`'s `DSCP_MAP`) —
   traffic is tagged with a DSCP value per type (EF for real-time voip/video/gaming/streaming,
   AF21 for interactive web/https/dns/ssh, CS1/BE for bulk/background); each link's flat TCLink
   netem queue is replaced post-`net.start()` with a 3-band HTB+netem DiffServ hierarchy
   (`apply_qos_qdiscs()`) that classifies by TOS byte, preserving each link's original
   delay/loss/jitter/queue-depth per band.
4. **NAT** (`network_build.py`, `setup_nat_gateway` + `NATMonitor`) — topologies that declare
   `nat_private_subnet` and a host with `role: "gateway"` get a one-armed NAT router: private
   hosts default-route through it, it SNATs via iptables to its own address. `NATMonitor` polls
   `/proc/net/nf_conntrack` on the gateway host and logs translations to
   `nat_translations.jsonl`. NAT is generic/topology-agnostic — any topology can opt in by
   setting those two fields; `datacenter` currently does not.

## Dataset output

`build_dataset()` (`dataset_builder.py`) reads raw JSONL from `$DATA_DIR/stats/*.jsonl` and
writes `$DATA_DIR/datasets/<name>.csv` + `.parquet` for each of: `transport_events`,
`flow_stats`, `port_stats`, `rtt`, `traffic_log`, `impairments`, `path_traces`, `dns_queries`,
`http_transactions`, `anomaly_events`, `connection_states`, `nat_translations` — plus a derived
`hop_details` table (one row per path-trace hop, exploded from `path_traces`) and a
`metadata.json` (topology snapshot, routing mode, traffic mix, impairment config). Files are
only written if their source JSONL has at least one row, so a topology without NAT (or a run
where NAT translation logging didn't produce output — see limitations below) simply won't have
a `nat_translations.csv`.

## Known limitations

- **NAT translations are unreliable inside Docker.** `NATMonitor` reads
  `/proc/net/nf_conntrack` on the NAT gateway host to log translations. In at least one
  Docker-based collection run in this repo (`results/combined/`, built from `five_as` runs),
  `nat_translations.csv` never appears even though other topologies in the same batch do have
  NAT hosts — likely because the container's netfilter/conntrack visibility differs from a bare
  Ubuntu host even under `--privileged`. Don't assume `nat_translations.csv` exists for a given
  run; check for it before depending on it.
- **STP convergence on looped topologies.** Topologies with redundant switch links (e.g.
  `five_as` has two independent loops) need OVS spanning-tree to converge before `pingAll`/QoS/
  NAT setup are reliable, or you see transient connectivity loss. `light_simulation.py`'s
  `main()` scales the STP wait by two independent factors: the number of excess links
  (`excess_links = len(topo["links"]) - (len(topo["switches"]) - 1)`, contributing
  `15 * (excess_links - 1)` seconds) **and** the switch-level graph diameter (via
  `netutil.graph_diameter()`, contributing `4 * diameter` seconds) — combined as
  `30 + 15*(excess_links-1) + 4*diameter` when `excess_links > 0`. The diameter term was added
  this session specifically to address a reproducible bug where the host on the "core" switch
  couldn't reach far-side hosts even after the loop-count-only wait — loop count alone doesn't
  capture how far a BPDU has to propagate on a topology's longest path. Neither the original
  loop-count fix nor this diameter addition has been re-verified against a live Mininet run
  this session (no Mininet available on this dev machine) — treat it as "believed fixed,
  unverified this session" rather than confirmed, and re-check pingAll loss output on the next
  real run before relying on it.
