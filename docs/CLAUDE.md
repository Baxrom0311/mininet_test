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

## NetLab (`lab/`) — interactive visual lab

A separate, self-contained app (does **not** touch the dataset pipeline above): a
Packet-Tracer/OPNET-style UI where the user builds a topology and runs it live on a
*real* Linux stack. See `lab/README.md` (Uzbek) for usage.

- `lab/netlab.py` — CLI entry (`python3 -m lab.netlab [--driver demo|real] [--host] [--window]`).
- `lab/server.py` — FastAPI REST + `/ws/events` (packet/stats/log/state events) + `/ws/term/<dev>` (PTY).
  Binds 127.0.0.1 by default; any other host forces a token (real driver gives root shells).
- `lab/model.py` — project JSON model + `validate_project()`. Invariants: device names `^[a-z][a-z0-9]{0,7}$`
  (Linux ifname limit), interface `<dev>-eth<N>` == OpenFlow/BMv2 port N, deterministic MACs
  `00:00:00:<id hi>:<id lo>:<port>` on namespace devices, dpid == device `id`.
- `lab/drivers/mininet_driver.py` — real driver. Network is kept alive; devices/links/link params/IPs are
  applied **live** (no restart). Switches are started individually with *their own* controller (never
  `net.start()`, which would attach every switch to every controller). FRR daemons run per router with
  `-N <name>` pathspace (`vtysh -N <name>`). P4 switches start `--no-p4` and load programs via
  `load_new_config_file`+`swap_configs`. Capture = one `tcpdump -i any` per netns (+root), keep only
  `Out` lines → each packet maps to exactly one link/direction. Stats from `/proc/<pid>/net/dev`.
  All Mininet ops hold `driver.lock` (Node.cmd is not thread-safe). Mininet imports are lazy.
- `lab/drivers/demo_driver.py` — Mininet-free simulation (BFS L2 domains, LPM, dynamic-routing
  approximation) used for UI work, Windows, and CI (`lab/tests/smoke_demo.py`).
- `lab/ui/` — the main UI: Vite + React 18 + TypeScript + Tailwind + hand-written shadcn/ui components
  (Radix). State in `src/lib/store.ts` (zustand; project model is the source of truth, ops sync live to the
  backend when running), backend events via `src/lib/bus.ts` (packet animation is imperative, outside
  React). Built output `lab/web-react/` IS committed (users need no Node); server serves it at `/` and the
  legacy vanilla UI (`lab/web/`) at `/classic`. CI rebuilds and fails if `lab/web-react` is stale — run
  `cd lab/ui && npm run build` after UI changes. Chart colours read CSS vars; components subscribe to
  `store.theme` so they re-read on theme switch.
- `lab/web/` — legacy vanilla JS UI (`/classic`); reuses `studio/web/css` via `/shared`; vendored xterm.
- Scenarios (`project.scenarios`: apps udp/tcp/ping + timed link events) run on three engines via
  `lab/engines/manager.py` → `lab/results/<project>/<run_id>.json` (gitignored): `analytic.py` (flow
  model, in-process), `ns3_runner.py` (ns-3 DES via the `ns3` PyPI wheel, **separate process** — the
  Simulator is a global singleton and NS_FATAL aborts the process; use `SetAttributeFailSafe` for any
  attribute that may not exist), `emulation.py` (iperf3/ping on the live real-driver network). Switch
  links in ns-3 use SimpleNetDevice/SimpleChannel, not CSMA: CSMA is half-duplex and holds the channel
  for the propagation delay, capping throughput at ms-scale delays. ns-3 TCP is set to Linux-like
  defaults (Cubic, MSS 1448, 4/6 MB buffers) so engines are comparable.
- Parametric studies (`POST /api/studies`, `RunManager.start_study`): optional `param2`/`values2` runs the
  full grid (<= 49, results carry `study_param2/study_value2/study_index2`, UI draws a heatmap); one param (link bw/delay/loss/queue
  or app rate/size) × N values, run sequentially; each run stores `study_id/study_param/study_value`.
  Link params are baked into the project copy (analytic/ns-3) or injected as a t=0 `link_params` event
  (mininet, live network). ns-3: routers with `ripd` (and no ospfd/isisd) get ns-3 RipHelper; others use
  global SPF. Don't set `IpForward=false` on single-homed hosts — ns-3.44 global routing segfaults on a
  P2P-attached non-forwarding host. Analytic engine models RIP convergence as stale pre-failure routes
  for `rip_convergence_s` (default 15).
- Wi-Fi: device type `wifiap` (L2) + links with `params.medium == "wifi"` (AP <-> host/server/router, one
  per station). Radio model in `lab/wifi.py` (log-distance n=3, SNR->MCS tables calibrated to ns-3, link if
  RSSI >= -82 dBm); the UI re-implements the formula in `lab/web/js/wifi.js` from tables served in
  `/api/info.wifi` — keep them in sync. Distances = canvas px * `project.meters_per_px`. ns-3: one
  YansWifi channel per AP, IdealWifiManager, AP device bridged with its wired ports. Analytic: per-station
  capacity from the model + per-AP airtime resource with airtime-fair TCP sharing. Mininet: AP = OVS
  switch, Wi-Fi link = TCLink at the modelled rate (no shared medium).
- ns-3 routing no longer uses Ipv4GlobalRouting (segfaults on IpForward=false P2P hosts; aborts with "L2
  forwarding loop" when several APs share one YansWifiChannel and are bridged to the same LAN). Instead
  `install_routes()` installs `Net.routes()` (same model as analytic) as static routes, re-run on link
  events and when a moving station's Wi-Fi link goes in/out of range. RIP routers keep ns-3 RIP.
- Wi-Fi multi-AP: all APs of a band share ONE YansWifiChannel; per-AP `channel` (validated against
  `wifi.CHANNELS`) separates them at the PHY. Analytic: co-channel APs within link range form one airtime
  pool (union-find). Mobility: `move` events (`lab/mobility.py`, canvas px, linear) — ns-3 updates
  positions every 0.5 s (samples stay 1 s; rate uses time since last sample), analytic per step,
  mininet via `MininetDriver.wifi_refresh()` each second.
- Roaming: ESS = same (SSID, band, channel) (`wifi.ess_key`). ns-3 StaWifiMac re-associates by itself;
  the runner polls `GetBssid(0)` each sample and emits `roam` events. APs without stations are still
  installed (beacons). Analytic: sticky client — best AP at t=0, then 1 step outage after the current
  link fails, then re-point the link to the strongest ESS AP. Mininet: no roaming.
- The real driver could not be executed in CI/this repo's sandbox (no Mininet/OVS/root netns); it was
  checked against Mininet 2.3 sources only. Treat first real runs as integration testing.

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
