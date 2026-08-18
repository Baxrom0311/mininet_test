# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Mininet + SDN network simulation for generating ML/AI training datasets (flow records, DNS, HTTP, TCP states, anomalies/attacks). All code comments, CLI help text, and print output are in Uzbek.

There are **three independent, non-interoperating generations** of the simulator living side by side. Know which one you're touching before editing:

1. **`main.py` + `ryu_transport_controller.py`** — minimal 9-switch Mininet lab with a Ryu/os-ken remote controller. Self-contained smoke test (`--mode test`) that pings all hosts, sends a TCP + UDP sample, and asserts both protocols were logged by the controller.
2. **`light_simulation.py`** (single ~2300-line file, current/active generation — see `TOPOLOGIES`/routing code) — the primary simulator. No ONOS, no Docker required for direct-server use; embeds its own os-ken/Ryu controller app (`TransportMonitor`) in-process via `start_controller()`. This is what `Dockerfile` / `docker-entrypoint.sh` run.
3. **`run_simulation.py` + `realistic_internet/` package** — older v3 design built around an external **ONOS** controller running in Docker, with a fixed 5-AS/24-switch topology (`RealisticInternetTopo`), sFlow-RT collection, and pcap-based dataset building. Requires Docker + the `onosproject/onos:2.7.0` image; check `check_dependencies()` in `run_simulation.py` for the full prerequisite list.

`test_basic.py` is a fourth, throwaway 2-switch/2-host smoke test that writes a controller app to `/tmp` and runs it standalone — useful as a minimal reference for the os-ken controller API but not part of the main flow.

When asked to add a topology, routing algorithm, traffic type, or anomaly, first confirm whether the ask targets `light_simulation.py` (most likely, per recent commit history) or the `realistic_internet/` ONOS package — the two do not share code.

## Commands

All simulation entry points require root (`sudo`) because Mininet manipulates network namespaces/OVS.

```bash
# Install (bare Ubuntu 22.04+/24.04, no Docker) — light generation only
sudo bash install_light.sh

# Install (full v3/ONOS generation, heavier)
sudo bash install.sh

# --- light_simulation.py (primary generation) ---
sudo python3 light_simulation.py                                          # default: three_as + l2_learn, 180s
sudo python3 light_simulation.py --topology five_as --routing ospf --duration 300
sudo python3 light_simulation.py --topology datacenter --routing ecmp
sudo python3 light_simulation.py --topology campus --routing bgp --duration 300
sudo python3 light_simulation.py --topology three_as --cli                # Mininet CLI, no auto traffic
python3 light_simulation.py --dataset-only                                # rebuild CSVs from previous run's raw data, no root/network needed
python3 light_simulation.py --topology five_as --routing spf --visualize  # PNG topology map, no root needed
sudo python3 light_simulation.py --no-traffic --no-impairments            # topology + routing only

# --- main.py (9-switch Ryu/os-ken smoke lab) ---
sudo python3 main.py --mode test     # pingAll + TCP/UDP sample + controller log assertions, exit code signals pass/fail
sudo python3 main.py --mode cli      # interactive Mininet CLI over the 9-switch lab

# --- test_basic.py (throwaway 2-switch minimal check) ---
sudo python3 test_basic.py

# --- run_simulation.py (v3, ONOS + Docker) ---
sudo python3 run_simulation.py                       # 5 min run
sudo python3 run_simulation.py --duration 1800 --cli
sudo python3 run_simulation.py --dataset-only         # build dataset from existing /data without touching the network

# Docker (light_simulation.py packaged; needs --privileged for Mininet)
docker build -f docker/Dockerfile -t internet-sim .
docker run --privileged --rm -v $(pwd)/output:/data internet-sim --topology five_as --routing spf --duration 300
docker run --privileged --rm -it -v $(pwd)/output:/data internet-sim --topology campus --routing spf --cli
```

There is no pytest/unittest suite — "tests" are the runnable scripts above (`main.py --mode test`, `test_basic.py`) that assert behavior via exit codes and printed checks, run against a real (simulated) network.

Cleanup between runs matters: Mininet/OVS state from a crashed run will break the next one. `light_simulation.py` and `run_simulation.py` both call `mininet.clean.cleanup()` / `mn -c` at start; if a run is interrupted outside these scripts, run `sudo mn -c` manually before retrying.

## Architecture (light_simulation.py — the file to know)

Single-file design; key sections in order of appearance:

- `TOPOLOGIES` dict — 4 topologies: `three_as` (3 AS, 6 switches), `five_as` (5 AS, 9 switches), `datacenter` (fat-tree, 6 switches), `campus` (7 switches). Each entry defines switches, hosts, links, and AS membership.
- `TRAFFIC_MIX` / `ANOMALY_MIX` / `IMPAIRMENT_EVENTS` — declarative profiles for 13 traffic types, 8 attack types, 10 impairment types (packet loss, delay spikes, bandwidth limits, link flap, congestion, reorder, buffer bloat, MTU blackhole, duplicate, jitter spike).
- **Routing engine** (`compute_paths()` dispatches by `--routing` mode) — 10 modes total: `l2_learn` (reactive MAC-learning flood, the switch-side default when no controller-side path precomputation applies), `rip`, `ospf`, `isis`, `eigrp`, `bgp`, `ecmp`, `spf`, `policy`, `static`. Path math lives in `compute_dijkstra()`, `_dijkstra_weighted()`, `_bfs_shortest()`/`_bfs_all_paths()`, plus protocol-specific cost functions (`_compute_ospf_cost`, `_compute_isis_metric`, `_compute_eigrp_metric`, `_compute_rip_metric`, `_bgp_path_score`, `_bgp_route_dampening`) and area/level assignment helpers (`_assign_ospf_areas`, `_assign_isis_levels`). Non-`l2_learn` modes precompute paths and push flow rules; `l2_learn` relies on the controller's reactive flooding/learning instead.
- `TransportMonitor` (`class`, subclasses os-ken/Ryu `BaseApp`) — the in-process SDN controller app, structurally identical to the standalone `ryu_transport_controller.py` (L2 learning, per-flow OpenFlow rule install, periodic flow-stats polling). Started via `start_controller()`; controller and Mininet run in the same process rather than as separate manager subprocess like `main.py` does.
- `build_topology()` — turns a `TOPOLOGIES` entry into a Mininet `Topo`/`Mininet` instance.
- `TrafficGen` — one thread-loop method per traffic mechanism: `_iperf_loop`, `_ping_loop`, `_http_loop`, `_dns_loop` (with cache/TTL/recursive resolution), `_anomaly_loop` (attack traffic), `_connection_state_loop` (TCP state tracking), `_adaptive_bitrate_loop` (video ABR).
- `Impairments` — applies/varies the 10 impairment types on live links during a run.
- `Collector` / `PathTracer` — capture raw events (JSONL) and compute hop-by-hop paths (Dijkstra/BFS) for tracing.
- `build_dataset()` — converts collected JSONL into the 12 CSV outputs under `/data/datasets/` (`flow_records.csv`, `flow_stats.csv`, `ping_results.csv`, `iperf_results.csv`, `traceroute_hops.csv`, `link_stats.csv`, `impairment_events.csv`, `topology_snapshot.csv`, `dns_queries.csv`, `http_transactions.csv`, `anomaly_events.csv`, `connection_states.csv`). Runnable standalone via `--dataset-only` against data left by a previous run.
- `visualize_topology()` — matplotlib/networkx PNG rendering of a topology + routing choice, runnable without root via `--visualize`.

Note: the README documents only 4 routing algorithms (l2_learn, SPF, ECMP, policy); the actual `--routing` choices list has grown to 10 (rip/ospf/isis/eigrp/bgp added) per the most recent commit — trust the code/`--help` epilog over the README table when they disagree.

### Controller ↔ simulation IPC

Both `light_simulation.py`'s embedded controller and the standalone `ryu_transport_controller.py` write append-only JSONL to fixed paths that the Mininet-side process later reads back:
- `/tmp/ryu_transport_events.jsonl` — per-packet transport metadata (TCP/UDP/ICMP/ARP fields)
- `/tmp/ryu_flow_stats.jsonl` — periodic OpenFlow flow-stats polls (every 5s via `hub.spawn`)

`main.py` reads these same paths back after traffic generation to assert TCP/UDP events were captured — this file-based handoff is the integration point between controller and topology code, not a shared Python object.

The controller code itself tries `ryu` first and falls back to `os_ken` (`from ryu... except ImportError: from os_ken...`) — both are wire-compatible OpenFlow 1.3 controller frameworks; os-ken is Ryu's actively maintained fork, packaged as `python3-os-ken` on Ubuntu.

## Architecture (realistic_internet/ — v3 ONOS generation)

- `config.py` — all configuration in one place: ONOS/sFlow-RT container settings, `AS_DEFINITIONS` (5 AS: Tier-1 core, regional ISP, CDN, enterprise, residential), `INTER_AS_LINKS`, and `LINK_PROFILES` (named bandwidth/delay/jitter/loss presets like `tier1_core`, `tier1_border`, `isp_core`, mirroring real-world link classes).
- `topology.py` — `RealisticInternetTopo` builds the fixed 5-AS/24-switch/~32-host topology from `config.py` definitions; writes topology metadata JSON to `/data/stats/topology_metadata.json` for later dataset joins.
- `onos_manager.py` — manages the ONOS Docker container lifecycle (start, wait-ready, wait-for-discovered-topology, app activation).
- `traffic_generator.py` — `TrafficOrchestrator`, analogous role to `light_simulation.py`'s `TrafficGen`.
- `impairments.py` — `ImpairmentManager`, static + dynamic link impairment injection.
- `data_collector.py` — `DataCollector` pulls from ONOS REST API, sFlow-RT, and tcpdump/pcap in parallel threads into `/data/{pcap,stats,flows,onos_logs,sflow_data}`.
- `dataset_builder.py` — `DatasetBuilder.build_all()` turns collected pcap/ONOS/sFlow data into ML-ready CSV/Parquet under `/data/datasets/`.

`run_simulation.py` is the orchestrator: checks Docker + ONOS image present, cleans previous Mininet/OVS state, starts ONOS, builds the network, waits for ONOS to discover the expected device/link count, then wires collector → traffic → impairments → timed run → teardown → dataset build, with a SIGINT/SIGTERM handler that tears everything down cleanly.
