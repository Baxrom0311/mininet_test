"""Topologiya generatorlari (OPNET "Rapid Configuration"): katta tarmoqlarni bir buyruq bilan.

    python3 -m lab.topogen ring --routers 20 --hosts 5 --flows 40 > big.json
    POST /api/topogen {"kind": "campus", "core": 2, "dist": 4, "access": 8, "hosts": 10}

Turlari:
  ring    — k router halqa (+ har 3-chi routerdan qarama-qarshi tomonga diagonal), OSPF;
            har routerda LAN switch + h host.
  star    — markaziy router, k filial router (yulduz), har filialda LAN.
  tree    — ikkilik daraxt (chuqurlik d), barglarda LAN.
  campus  — 3 qatlamli kampus: core routerlar (to'liq bog'langan), distribution routerlar
            (har biri 2 ta core'ga), access switch'lar (har biri bitta dist'ga), hostlar.
Barcha routerlarda OSPF (hello 1 s), kabellar: router-router `core_bw`, LAN `lan_bw`.
`flows` — deterministik juftlar bilan UDP/TCP aralash "asosiy" ssenariy.
"""

import argparse
import json
import sys


class _B:
    def __init__(self, name):
        self.p = {"version": 1, "name": name, "devices": {}, "links": {}, "p4_programs": {},
                  "controllers": {}, "scenarios": {}}
        self.nid = 0
        self.lid = 0
        self.port = {}
        self.net = 0
        self.hosts = []

    def dev(self, name, t, cfg, x=0, y=0):
        self.nid += 1
        self.p["devices"][name] = {"id": self.nid, "type": t, "x": x, "y": y, "config": cfg}

    def np(self, d):
        self.port[d] = self.port.get(d, 0) + 1
        return self.port[d]

    def link(self, a, pa, b, pb, bw):
        self.lid += 1
        self.p["links"][f"l{self.lid}"] = {"a": {"dev": a, "port": pa}, "b": {"dev": b, "port": pb},
                                           "params": {"bw": bw, "delay": "1ms", "jitter": "0ms", "loss": 0,
                                                      "queue": 1000}, "up": True}

    def router(self, name, x=0, y=0):
        self.dev(name, "router", {"daemons": ["ospfd"], "interfaces": {}, "startup": ""}, x, y)

    def rif(self, r, p, ip):
        self.p["devices"][r]["config"]["interfaces"][f"{r}-eth{p}"] = {"ip": ip}

    def p2p(self, a, b, bw):
        self.net += 1
        if self.net > 16000:
            raise ValueError("juda ko'p router-router kabeli (> 16000)")
        hi, lo = divmod(self.net, 64)
        pa, pb = self.np(a), self.np(b)
        self.rif(a, pa, f"172.{16 + hi // 256}.{hi % 256}.{lo * 4 + 1}/30")
        self.rif(b, pb, f"172.{16 + hi // 256}.{hi % 256}.{lo * 4 + 2}/30")
        self.link(a, pa, b, pb, bw)

    def lan(self, r, idx, hosts, bw, sw=None, x=0, y=0):
        """r router ostida /24 LAN (10.<idx//256>.<idx%256>.0/24), switch va hostlar."""
        if idx >= 65536:
            raise ValueError("LAN'lar soni juda ko'p")
        a, b = divmod(idx, 256)
        sw = sw or f"s{idx}"
        self.dev(sw, "switch", {"mode": "standalone", "stp": False}, x, y + 60)
        pr, ps = self.np(r), self.np(sw)
        self.rif(r, pr, f"10.{a}.{b}.1/24")
        self.link(r, pr, sw, ps, bw)
        for j in range(hosts):
            n = f"h{idx}x{j}"
            if len(n) > 8:
                raise ValueError("host nomi 8 belgidan oshdi — kamroq LAN/host tanlang")
            self.dev(n, "host", {"interfaces": {f"{n}-eth1": {"ip": f"10.{a}.{b}.{10 + j}/24"}},
                                 "gateway": f"10.{a}.{b}.1", "routes": []}, x + 30 * j, y + 120)
            self.link(sw, self.np(sw), n, 1, bw)
            self.hosts.append(n)

    def ospf_startup(self):
        """Real FRR uchun: har router — `router ospf` + har interfeys subneti `network ... area 0`;
        router-router (/30) interfeyslarda point-to-point va tez hello (1/4 s)."""
        import ipaddress
        for name, d in self.p["devices"].items():
            if d["type"] != "router":
                continue
            ifs = d["config"]["interfaces"]
            lines = [f"hostname {name}", "!"]
            for ifn, ic in sorted(ifs.items()):
                if ipaddress.ip_interface(ic["ip"]).network.prefixlen == 30:
                    lines += [f"interface {ifn}", " ip ospf network point-to-point",
                              " ip ospf hello-interval 1", " ip ospf dead-interval 4", "!"]
            rid = ipaddress.ip_address(f"1.1.{d['id'] // 256}.{d['id'] % 256}")
            lines += ["router ospf", f" ospf router-id {rid}"]
            for ifn, ic in sorted(ifs.items()):
                lines.append(f" network {ipaddress.ip_interface(ic['ip']).network} area 0")
            lines.append("!")
            d["config"]["startup"] = "\n".join(lines)

    def finish(self, flows, rate="5Mbps", duration=30):
        self.ospf_startup()
        apps, hs = [], self.hosts
        for f in range(flows):
            if len(hs) < 2:
                break
            a, b = hs[(f * 7) % len(hs)], hs[(f * 13 + 5) % len(hs)]
            if a == b:
                b = hs[(hs.index(a) + 1) % len(hs)]
            app = {"id": f"f{f}", "type": "udp" if f % 2 else "tcp", "src": a, "dst": b,
                   "start": 1, "stop": duration - 1}
            if app["type"] == "udp":
                app.update(rate=rate, size=1000)
            apps.append(app)
        if apps:
            self.p["scenarios"]["asosiy"] = {"duration": duration, "apps": apps, "events": [],
                                             "notes": f"generator: {len(apps)} oqim"}
        self.p["notes"] = (f"generator: {sum(1 for d in self.p['devices'].values() if d['type'] == 'router')} router, "
                           f"{len(self.hosts)} host, {len(self.p['links'])} kabel")
        return self.p


def ring(routers=10, hosts=3, flows=20, core_bw=1000, lan_bw=100, name="ring"):
    import math
    b = _B(name)
    k = max(3, int(routers))
    for i in range(k):
        ang = 2 * math.pi * i / k
        b.router(f"r{i}", int(600 + 400 * math.cos(ang)), int(400 + 300 * math.sin(ang)))
    for i in range(k):
        b.p2p(f"r{i}", f"r{(i + 1) % k}", core_bw)
    for i in range(0, k // 2, 3):
        if (i + k // 2) % k != (i + 1) % k:
            b.p2p(f"r{i}", f"r{(i + k // 2) % k}", core_bw)
    for i in range(k):
        d = b.p["devices"][f"r{i}"]
        b.lan(f"r{i}", i, int(hosts), lan_bw, x=d["x"], y=d["y"])
    return b.finish(int(flows))


def star(branches=8, hosts=3, flows=20, core_bw=100, lan_bw=100, name="star"):
    b = _B(name)
    b.router("hq", 600, 100)
    for i in range(int(branches)):
        b.router(f"r{i}", 100 + 140 * i, 300)
        b.p2p("hq", f"r{i}", core_bw)
        b.lan(f"r{i}", i, int(hosts), lan_bw, x=100 + 140 * i, y=300)
    return b.finish(int(flows))


def tree(depth=3, hosts=3, flows=20, core_bw=1000, lan_bw=100, name="tree"):
    b = _B(name)
    depth = max(1, int(depth))
    b.router("r1", 600, 60)
    leaves = ["r1"]
    idx = 1
    for lvl in range(1, depth):
        nxt = []
        for parent in leaves:
            for _c in range(2):
                idx += 1
                n = f"r{idx}"
                b.router(n, 60 * idx, 60 + 140 * lvl)
                b.p2p(parent, n, core_bw)
                nxt.append(n)
        leaves = nxt
    for i, r in enumerate(leaves):
        d = b.p["devices"][r]
        b.lan(r, i, int(hosts), lan_bw, x=d["x"], y=d["y"])
    return b.finish(int(flows))


def campus(core=2, dist=4, access=8, hosts=5, flows=40, core_bw=10000, dist_bw=1000, lan_bw=100, name="campus"):
    b = _B(name)
    cores = [f"c{i}" for i in range(int(core))]
    for i, c in enumerate(cores):
        b.router(c, 400 + 300 * i, 60)
    for i in range(len(cores)):
        for j in range(i + 1, len(cores)):
            b.p2p(cores[i], cores[j], core_bw)
    dists = [f"d{i}" for i in range(int(dist))]
    for i, d in enumerate(dists):
        b.router(d, 150 + 200 * i, 220)
        for c in cores[:2]:
            b.p2p(d, c, dist_bw)
    for a in range(int(access)):
        d = dists[a % len(dists)]
        b.lan(d, a, int(hosts), lan_bw, sw=f"a{a}", x=100 + 120 * a, y=380)
    return b.finish(int(flows))


KINDS = {"ring": ring, "star": star, "tree": tree, "campus": campus}


def generate(kind, **kw):
    if kind not in KINDS:
        raise ValueError(f"tur {tuple(KINDS)} dan biri")
    ipv6 = kw.pop("ipv6", False)
    p = KINDS[kind](**kw)
    if len(p["devices"]) > 3000:
        raise ValueError("juda katta (> 3000 qurilma)")
    return add_ipv6(p) if ipv6 else p


def add_ipv6(project):
    """Add ULA /64s alongside generated IPv4 LAN/transit nets.

    OSPFv3 is a hop-based model in analytic/ns-3, not a live protocol there.
    The original IPv4 scenarios and FRR startup are preserved.
    """
    import copy
    import ipaddress
    p = copy.deepcopy(project)
    networks, addresses = {}, {}
    for d in p["devices"].values():
        cfg = d["config"]
        for ic in cfg.get("interfaces", {}).values():
            ii = ipaddress.ip_interface(ic["ip"])
            subnet = networks.setdefault(ii.network, len(networks) + 1)
            # ns-3 intentionally drops forwarded RFC 3849 documentation addresses.
            address = ipaddress.IPv6Address(int(ipaddress.IPv6Address(f"fd00:1:{subnet:x}::"))
                                           + int(ii.ip) - int(ii.network.network_address))
            ic["ip6"] = f"{address}/64"
            addresses[str(ii.ip)] = str(address)
        if d["type"] == "router":
            cfg["daemons"] = list(dict.fromkeys(cfg.get("daemons", []) + ["ospf6d"]))
    for d in p["devices"].values():
        cfg = d["config"]
        if cfg.get("gateway"):
            cfg["gateway6"] = addresses[cfg["gateway"]]
    for name, sc in list(p["scenarios"].items()):
        p["scenarios"][name + "_ipv6"] = {**copy.deepcopy(sc), "ip_version": 6}
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="NetLab topologiya generatori")
    ap.add_argument("kind", choices=sorted(KINDS))
    ap.add_argument("--routers", type=int)
    ap.add_argument("--branches", type=int)
    ap.add_argument("--depth", type=int)
    ap.add_argument("--core", type=int)
    ap.add_argument("--dist", type=int)
    ap.add_argument("--access", type=int)
    ap.add_argument("--hosts", type=int)
    ap.add_argument("--flows", type=int)
    ap.add_argument("--name")
    ap.add_argument("--ipv6", action="store_true", help="IPv6 /64 + IPv6 scenario nusxasi")
    a = ap.parse_args(argv)
    kw = {k: v for k, v in vars(a).items() if k != "kind" and v is not None}
    json.dump(generate(a.kind, **kw), sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
