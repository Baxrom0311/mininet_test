"""Qurilma darajasidagi statistika (OPNET "node statistics"): IP forwarding,
tashlanishlar, routing jadvali va konvergensiya — barcha dvigatellar uchun umumiy.

Natija formati (run natijasida):
    nodes: {dev: [{"t", "routes", "fwd_pps", "rx_pps", "tx_pps", "noroute", "discards"}, ...]}
    route_events: [{"t", "dev", "added": [..], "removed": [..]}]
    convergence: [{"t", "action", "link", "converged_s", "last_change", "devices"}]

Marshrut kaliti — "prefix via|dev" satri (masalan "10.0.2.0/24 via 10.0.12.2",
"10.0.1.0/24 dev r1-eth1"); metric/protokol hisobga olinmaydi.
"""

import ipaddress
import socket
import struct

L3_NS = ("host", "server", "router")


def route_key(net, via, ifname):
    return f"{net} via {via}" if via else f"{net} dev {ifname}"


def read_proc_routes(pid):
    """/proc/<pid>/net/route -> {key} (IPv4, namespace ichidagi asosiy jadval)."""
    out = set()
    try:
        with open(f"/proc/{pid}/net/route") as f:
            lines = f.readlines()[1:]
    except OSError:
        return out
    for line in lines:
        v = line.split()
        if len(v) < 8:
            continue
        ifn, dst, gw, flags, mask = v[0], v[1], v[2], int(v[3], 16), v[7]
        if not flags & 1:          # RTF_UP
            continue
        h2ip = lambda h: socket.inet_ntoa(struct.pack("<I", int(h, 16)))  # noqa: E731
        try:
            net = ipaddress.ip_network(f"{h2ip(dst)}/{h2ip(mask)}", strict=False)
        except ValueError:
            continue
        via = h2ip(gw) if flags & 2 else None   # RTF_GATEWAY
        out.add(route_key(net, via, ifn))
    return out


def _proc_table(path, prefix):
    try:
        with open(path) as f:
            lines = [ln.split() for ln in f if ln.startswith(prefix)]
    except OSError:
        return {}
    if len(lines) < 2:
        return {}
    return {k: int(v) for k, v in zip(lines[0][1:], lines[1][1:]) if v.lstrip("-").isdigit()}


def read_proc_snmp(pid):
    """/proc/<pid>/net/snmp "Ip:" + netstat "IpExt:" -> dict (ForwDatagrams,
    InReceives, OutRequests, InDiscards, OutNoRoutes, InNoRoutes, ...)."""
    out = _proc_table(f"/proc/{pid}/net/snmp", "Ip:")
    if out:
        out.update(_proc_table(f"/proc/{pid}/net/netstat", "IpExt:"))
    return out


class RouteTracker:
    """Har namunada qurilma marshrutlar to'plamini solishtiradi -> route_events."""

    def __init__(self):
        self.prev = {}
        self.events = []

    def update(self, t, dev, routes):
        old = self.prev.get(dev)
        self.prev[dev] = routes
        if old is None or old == routes:
            return
        self.events.append({"t": round(float(t), 3), "dev": dev,
                            "added": sorted(routes - old), "removed": sorted(old - routes)})


def convergence(events, route_events, duration):
    """Har topologiya hodisasi uchun: shu hodisadan keyingi (keyingi hodisagacha)
    oxirgi marshrut o'zgarishigacha bo'lgan vaqt. O'zgarish bo'lmasa converged_s=None."""
    topo = sorted((e for e in events if e.get("action") in ("link_down", "link_up", "link_params", "move")),
                  key=lambda e: float(e.get("t", 0)))
    out = []
    for i, e in enumerate(topo):
        t0 = float(e.get("t", 0))
        t1 = float(topo[i + 1]["t"]) if i + 1 < len(topo) else float(duration) + 1e9
        win = [r for r in route_events if t0 - 1e-6 <= r["t"] < t1]
        last = max((r["t"] for r in win), default=None)
        out.append({"t": t0, "action": e.get("action"), "link": e.get("link") or e.get("device"),
                    "converged_s": round(last - t0, 3) if last is not None else None,
                    "last_change": last, "devices": sorted({r["dev"] for r in win})})
    return out


class SnmpSampler:
    """Soniyalik IP hisoblagichlari farqi -> node nuqtasi."""

    def __init__(self):
        self.prev = {}

    def point(self, t, dev, snmp, routes, dt=1.0):
        p0 = self.prev.get(dev)
        self.prev[dev] = snmp
        pt = {"t": float(t), "routes": len(routes)}
        if not p0 or not snmp:
            pt.update(fwd_pps=0, rx_pps=0, tx_pps=0, noroute=0, discards=0)
            return pt
        d = lambda k: max(snmp.get(k, 0) - p0.get(k, 0), 0)  # noqa: E731
        dt = max(dt, 1e-9)
        pt.update(fwd_pps=round(d("ForwDatagrams") / dt, 1), rx_pps=round(d("InReceives") / dt, 1),
                  tx_pps=round((d("OutRequests") + d("ForwDatagrams")) / dt, 1),
                  noroute=d("OutNoRoutes") + d("InNoRoutes"), discards=d("InDiscards") + d("OutDiscards"))
        return pt
