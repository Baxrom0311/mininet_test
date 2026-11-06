"""Demo driver — Mininet'siz, modelning o'zida soddalashtirilgan L2/L3 simulyatsiya.

Maqsad: UI'ni istalgan kompyuterda (Windows ham) ochib, topologiya qurish,
qurilmaga kirish, ping/traceroute bilan yo'lni ko'rish va paket animatsiyasini
sinash. Haqiqiy tarmoq emas — o'lchovlar uchun real driverdan foydalaning.

Soddalashtirishlar:
  * L2 domen: switch / P4 switch / SDN switch orqali BFS (802.1Q VLAN access/trunk; STP yo'q).
  * Host: ulangan subnet + default gateway (+ statik marshrutlar).
  * Router (FRR): ulangan + statik; dinamik daemon (ospfd/bgpd/ripd/isisd/...)
    yoqilgan routerlar bir-biridan barcha subnetlarni "o'rganadi" (hop soni).
  * SDN router: ulangan + statik marshrutlar.
"""

import copy
import ipaddress
import random
import re
import threading
import time
from collections import deque

from lab.model import (DEVICE_TYPES, device_mac, ifname, link_ports,
                       normalize_link_params, parse_ifname, switch_port_cfg, validate_project,
                       vlan_egress, vlan_ingress)
from lab.wifi import link_estimate as wifi_link_estimate
from .base import BaseDriver, DriverError, Terminal

DYNAMIC = {"ospfd": "OSPF", "bgpd": "BGP", "ripd": "RIP", "isisd": "IS-IS",
           "ospf6d": "OSPFv3", "eigrpd": "EIGRP"}


def _ms(v):
    m = re.match(r"([\d.]+)\s*(us|ms|s)?", str(v or "0"))
    if not m:
        return 0.0
    x = float(m.group(1))
    return {"us": x / 1000, "s": x * 1000}.get(m.group(2) or "ms", x)


class Net:
    """Loyiha modelidan L3 forwarding hisoblovchi yordamchi."""

    def __init__(self, project, resolve=True, ip_version=None):
        if ip_version is not None:
            from lab.addressing import project_for_family
            project = project_for_family(project, ip_version)
        if resolve:
            from lab.services import has_dhcp_clients, resolve_dhcp
            if has_dhcp_clients(project):
                project = resolve_dhcp(project)[0]     # "dhcp" -> deterministik lizing manzillari
        self.p = project
        self.devs = project["devices"]
        self._if_cache = {}
        versions = {ipaddress.ip_interface(ic["ip"]).version
                    for d in self.devs.values()
                    for ic in ((d.get("config") or {}).get("interfaces") or {}).values()
                    if ic.get("ip") and ic["ip"] != "dhcp"}
        self.ip_version = 6 if versions == {6} else 4
        self._stp = None
        self._lag = None
        self.flow_key = None     # bond hash kaliti (analitik: (manba, manzil, ilova)); None — manba qurilma
        self.l2_why = None

    def lag_members(self):
        """{(switch, port): bond guruhi} (lab.lag)."""
        if self._lag is None:
            from lab.lag import member_ports
            self._lag = member_ports(self.p)
        return self._lag

    def stp(self):
        """Spanning tree holati (lab.stp.compute) — blocking portlar va storm domenlari.
        project["_stp_hold"] — konvergensiya davomida hali forwarding bo'lmagan portlar."""
        if self._stp is None:
            from lab.stp import compute
            self._stp = compute(self.p, self.p.get("_stp_hold") or ())
        return self._stp

    def ifaces(self, dev):
        """[(port, ifname, IPv4Interface | IPv6Interface | None)] — fizik port + uning VLAN
        subinterfeyslari (`<dev>-eth<port>.<vlan>`, config.interfaces'da IP bilan)."""
        if dev in self._if_cache:
            return self._if_cache[dev]
        cfg = self.devs[dev].get("config") or {}
        ics = cfg.get("interfaces") or {}
        out = []
        for port in sorted(self.ports_of(dev)):
            base = ifname(dev, port)
            names = [base] + sorted((k for k in ics if k.startswith(base + ".")),
                                    key=lambda k: parse_ifname(k)[2] or 0)
            for ifn in names:
                ip = (ics.get(ifn) or {}).get("ip")
                try:
                    out.append((port, ifn, ipaddress.ip_interface(ip) if ip and ip != "dhcp" else None))
                except ValueError:
                    out.append((port, ifn, None))
        if self.devs[dev].get("type") == "router" and cfg.get("vrrp"):
            from lab.vrrp import vip_ifaces        # VRRP master: VIP — shu interfeysning qo'shimcha manzili
            out += vip_ifaces(self.p, dev)
        self._if_cache[dev] = out
        return out

    def owner_of(self, ip):
        for name in self.devs:
            for port, ifn, ipi in self.ifaces(name):
                if ipi and ipi.ip == ip:
                    return name, port
        return None, None

    # ── keshlar: Net qisqa umrli (analitik har qadamda yangisini yaratadi); topologiya
    # (kabel uchlari) shu davrda o'zgarmaydi, kabel holati (up/Wi-Fi) esa keshlanmaydi ──
    def _topo(self):
        if getattr(self, "_topo_idx", None) is None:
            on, ports = {}, {}
            for lid, l in self.p["links"].items():
                a, b = l["a"], l["b"]
                on.setdefault((a["dev"], a["port"]), (lid, b["dev"], b["port"], "ab"))
                on.setdefault((b["dev"], b["port"]), (lid, a["dev"], a["port"], "ba"))
                ports.setdefault(a["dev"], {})[a["port"]] = (lid, b["dev"], b["port"])
                ports.setdefault(b["dev"], {})[b["port"]] = (lid, a["dev"], a["port"])
            self._topo_idx = (on, ports)
        return self._topo_idx

    def ports_of(self, dev):
        """link_ports(project, dev) bilan bir xil ({port: (lid, peer, peer_port)}), indekslangan."""
        return self._topo()[1].get(dev, {})

    def link_on(self, dev, port):
        return self._topo()[0].get((dev, port))

    def is_l2(self, dev):
        t = self.devs[dev]["type"]
        return t in ("switch", "p4switch", "wifiap")

    def link_ok(self, lid):
        """Kabel ishlaydimi: UP va (Wi-Fi bo'lsa) signal yetarli."""
        l = self.p["links"].get(lid)
        if not l or l.get("up") is False:
            return False
        if (l.get("params") or {}).get("medium") == "wifi":
            est = wifi_link_estimate(self.p, lid)
            return bool(est and est["ok"])
        return True

    def l2_reach(self, dev, port, tag=None):
        """Keshlangan (_l2_reach_raw). LAG bo'lsa natija oqim hash'iga (flow_key) bog'liq."""
        key = (dev, port, tag, self.flow_key if self.lag_members() else None)
        c = self.__dict__.setdefault("_l2_cache", {})
        if key not in c:
            before = self.l2_why
            self.l2_why = None
            out = self._l2_reach_raw(dev, port, tag)
            c[key] = (out, self.l2_why)
            if self.l2_why is None:
                self.l2_why = before
        out, why = c[key]
        if why is not None:
            self.l2_why = why
        return {k: list(v) for k, v in out.items()}

    def _l2_reach_raw(self, dev, port, tag=None):
        """dev:port'dan (802.1Q teg `tag`, None = tegsiz) chiqqan kadr L2 domenda yetadigan
        L3 so'nggi nuqtalar: {(dev, port, tag_kelganda): [(link_id, dir), ...]}.
        Switch portlari access/trunk qoidalari bo'yicha (model.vlan_ingress/egress)."""
        out = {}
        first = self.link_on(dev, port)
        if not first or not self.link_ok(first[0]):
            return out
        st = self.stp()
        lag = self.lag_members()
        q = deque([(first[1], first[2], tag, [(first[0], first[3])])])
        seen = set()
        while q:
            cur, inport, wtag, path = q.popleft()
            if not self.is_l2(cur):
                out.setdefault((cur, inport, wtag), path)
                continue
            if cur in st["storm"]:          # L2 halqa STP'siz — kadrlar cheksiz aylanadi
                self.l2_why = (f"{cur}: L2 halqa (STP o'chiq): broadcast storm "
                               f"[{', '.join(st['storm_cycle'].get(cur, []))}]")
                return {}
            if (cur, inport) in st["blocked"]:  # STP blocking port kadrni qabul qilmaydi
                continue
            cd = self.devs[cur]
            v = vlan_ingress(switch_port_cfg(cd, inport), wtag)
            if v is None or (cur, v) in seen:
                continue
            seen.add((cur, v))
            for p2 in sorted(self.ports_of(cur)):
                if p2 == inport:
                    continue
                ok, etag = vlan_egress(switch_port_cfg(cd, p2), v)
                if not ok or (cur, p2) in st["blocked"]:
                    continue
                g = lag.get((cur, p2))
                if g is not None:      # bond: oqim faqat hash tanlagan bitta a'zodan o'tadi
                    from lab.lag import choose, usable_members
                    pick = choose(g, usable_members(self.p, g, self.link_ok), dev, self.flow_key)
                    side = "a" if g["a"][0] == cur else "b"
                    if pick is None or g["ports_" + side][pick] != p2:
                        continue
                    if not self.link_ok(pick):
                        self.l2_why = f"{cur}: bond {g[side][1]} a'zosi {pick} uzildi (failover kutilmoqda)"
                        continue
                nxt = self.link_on(cur, p2)
                if nxt and self.link_ok(nxt[0]):
                    q.append((nxt[1], nxt[2], etag, path + [(nxt[0], nxt[3])]))
        return out

    def dyn_daemons(self, dev):
        d = self.devs[dev]
        if d["type"] != "router":
            return set()
        # bgpd — IGP emas: alohida path-vector modeli (lab/bgp.py), faqat sozlangan qo'shnilar bilan
        supported = {"ospf6d", "isisd"} if self.ip_version == 6 else set(DYNAMIC) - {"bgpd", "ospf6d"}
        return set((d.get("config") or {}).get("daemons", [])) & supported

    def adjacency(self, dev):
        """L3 qo'shnilar: [(port, ifname, my_ip, neighbor, neighbor_ip, link_path)]"""
        out = []
        for port, ifn, ipi in self.ifaces(dev):
            if not ipi:
                continue
            for (nd, np, tg), path in self.l2_reach(dev, port, parse_ifname(ifn)[2]).items():
                for pp, nifn, nip in self.ifaces(nd):
                    if pp == np and parse_ifname(nifn)[2] == tg and nip and nip.network == ipi.network:
                        out.append((port, ifn, ipi, nd, nip, path))
        return out

    def routes(self, dev):
        """[(network, via_ip|None, ifname, proto)] — kichik prefiksdan kattasiga emas, LPM bilan ishlatiladi.
        connected/static/IGP + BGP (AD bo'yicha birlashtiriladi). Net davomida keshlanadi."""
        c = self.__dict__.setdefault("_rt_cache", {})
        if dev not in c:
            c[dev] = self._routes_raw(dev)
        return list(c[dev])

    def _routes_raw(self, dev):
        base = self._base_routes(dev)
        b = self.bgp()[0].get(dev)
        if not b:
            return base
        from lab.bgp import merge_routes
        return merge_routes(base, b)

    def bgp(self):
        """(router -> BGP marshrutlari, router -> sessiyalar) — bir marta hisoblanadi (kesh)."""
        if self.ip_version == 6:
            return {}, {}
        if getattr(self, "_bgp_cache", None) is None:
            from lab.bgp import compute
            self._bgp_cache = compute(self)
        return self._bgp_cache

    def _base_routes(self, dev):
        d = self.devs[dev]
        cfg = d.get("config") or {}
        rt = []
        for port, ifn, ipi in self.ifaces(dev):
            if ipi:
                link = self.link_on(dev, port)
                if link and self.link_ok(link[0]):
                    rt.append((ipi.network, None, ifn, "connected"))
        for r in cfg.get("routes") or []:
            try:
                net = ipaddress.ip_network(r["prefix"], strict=False)
                via = ipaddress.ip_address(r["via"])
            except (ValueError, KeyError):
                continue
            ifn = next((x[2] for x in rt if x[1] is None and via in x[0]), None)
            if ifn:
                rt.append((net, via, ifn, "static"))
        if d["type"] in ("host", "server") and cfg.get("gateway"):
            try:
                gw = ipaddress.ip_address(cfg["gateway"])
                ifn = next((x[2] for x in rt if x[1] is None and gw in x[0]), None)
                if ifn:
                    rt.append((ipaddress.ip_network("::/0" if gw.version == 6 else "0.0.0.0/0"), gw, ifn, "static"))
            except ValueError:
                pass
        mine = self.dyn_daemons(dev)
        if "ospfd" in mine:
            from lab.ospf import routes as ospf_routes
            rt += ospf_routes(self, dev, {x[0] for x in rt})
            mine = mine - {"ospfd"}
        if mine == {"isisd"}:
            from lab.isis import routes as isis_routes
            return rt + isis_routes(self, dev, {x[0] for x in rt})
        if mine:
            known = {x[0] for x in rt}
            # BFS dinamik routerlar grafida (hop soni = metric)
            q = deque([(dev, None, None, 0)])
            seen = {dev}
            while q:
                cur, first_via, first_if, hops = q.popleft()
                if cur != dev:
                    for port, ifn, ipi in self.ifaces(cur):
                        lk = self.link_on(cur, port)
                        if not lk or not self.link_ok(lk[0]):
                            continue        # o'chgan interfeys tarmog'i e'lon qilinmaydi (OSPF/RIP kabi)
                        if ipi and ipi.network not in known:
                            known.add(ipi.network)
                            proto = DYNAMIC[sorted(mine)[0]]
                            rt.append((ipi.network, first_via, first_if, f"{proto} [{hops}]"))
                for port, ifn, myip, nd, nip, _path in self.adjacency(cur):
                    if nd in seen or not (self.dyn_daemons(nd) & mine) or self._other_as(cur, nd):
                        continue
                    seen.add(nd)
                    q.append((nd, first_via or nip.ip, first_if or ifn, hops + 1))
        return rt

    def _other_as(self, a, b):
        """IGP AS chegarasidan o'tmaydi (ikkalasida BGP asn bor va har xil)."""
        from lab.bgp import bgp_cfg
        x, y = bgp_cfg(self.devs[a]), bgp_cfg(self.devs[b])
        return bool(x and y and x["asn"] != y["asn"])

    def lookup(self, dev, dst):
        c = self.__dict__.setdefault("_lk_cache", {})
        k = (dev, dst)
        if k not in c:
            c[k] = self._lookup_raw(dev, dst)
        return c[k]

    def _lookup_raw(self, dev, dst):
        best = None
        for net, via, ifn, proto in self.routes(dev):
            if dst in net and (best is None or net.prefixlen > best[0].prefixlen):
                best = (net, via, ifn, proto)
        return best

    def forward(self, src_dev, dst, max_hops=64):
        """src_dev'dan dst IP'ga yo'l: (ok, sabab, hops[], link_path[], delay_ms).
        hops: [(device, ip)], link_path: [(link_id, dir)]. Net davomida keshlanadi
        (LAG bo'lsa oqim hash'i — flow_key — kalitga kiradi)."""
        c = self.__dict__.setdefault("_fw_cache", {})
        k = (src_dev, dst, max_hops, self.flow_key if self.lag_members() else None)
        if k not in c:
            c[k] = self._forward_raw(src_dev, dst, max_hops)
        ok, why, hops, links, delay = c[k]
        return ok, why, list(hops), list(links), delay

    def _forward_raw(self, src_dev, dst, max_hops=16):
        cur = src_dev
        hops, links, delay = [], [], 0.0
        for _ in range(max_hops):
            for port, ifn, ipi in self.ifaces(cur):
                if ipi and ipi.ip == dst:
                    return True, "ok", hops, links, delay
            if hops and self.devs[cur]["type"] in ("host", "server"):
                return False, f"{cur}: host paketni uzatmaydi", hops, links, delay
            r = self.lookup(cur, dst)
            if not r:
                return False, f"{cur}: marshrut yo'q (Destination Net Unreachable)", hops, links, delay
            net, via, ifn, proto = r
            nh = via or dst
            _d, port, vl = parse_ifname(ifn)
            reach = self.l2_reach(cur, port, vl)
            target = None
            for (nd, np, tg), path in reach.items():
                for pp, nifn, nip in self.ifaces(nd):
                    if pp == np and parse_ifname(nifn)[2] == tg and nip and nip.ip == nh:
                        target = (nd, path, nip.ip)
            if not target:
                neighbor = "NDP" if dst.version == 6 else "ARP"
                return False, self.l2_why or f"{cur}: {nh} {neighbor} javob bermadi (Destination Host Unreachable)", hops, links, delay
            nd, path, nip = target
            links += path
            for lid, _dir in path:
                delay += _ms((self.p["links"][lid].get("params") or {}).get("delay", "1ms"))
            hops.append((nd, str(nip)))
            cur = nd
        return False, "TTL tugadi (marshrut halqasi?)", hops, links, delay

    def nat_chain(self, src_dev, dst, hops):
        """Yo'ldagi NAT routerlar: [(router, outside_ip)] — paket ichki interfeysdan kirib
        outside interfeysdan chiqqan joylar (manba shu IP'ga almashtiriladi)."""
        chain = []
        if dst.version == 6:
            return chain
        for nd, ip_in in hops[:-1]:          # oxirgi hop — manzil qurilmaning o'zi
            out = ((self.devs[nd].get("config") or {}).get("nat") or {}).get("outside")
            if not out:
                continue
            r = self.lookup(nd, dst)
            ingress = next((ifn for _p, ifn, ipi in self.ifaces(nd) if ipi and str(ipi.ip) == ip_in), None)
            if r and r[2] == out and ingress != out:
                oip = next((ipi.ip for _p, ifn, ipi in self.ifaces(nd) if ifn == out and ipi), None)
                if oip:
                    chain.append((nd, oip))
        return chain

    def reply(self, src_dev, src_ip, dst_dev, dst, hops):
        """dst_dev'dan src'ga javob yo'li (NAT hisobga olingan): forward() bilan bir xil kortej.
        NAT bo'lsa javob avval oxirgi NAT routerning outside IP'siga boradi, u ichkariga uzatadi."""
        chain = self.nat_chain(src_dev, dst, hops)
        if not chain:
            return self.forward(dst_dev, src_ip)
        targets = [(r, ip) for r, ip in reversed(chain)]
        cur, all_h, all_l, tot = dst_dev, [], [], 0.0
        for i, (r, ip) in enumerate(targets + [(src_dev, src_ip)]):
            ok, why, h, l, dl = self.forward(cur, ip)
            all_h += h; all_l += l; tot += dl
            if not ok:
                return False, why, all_h, all_l, tot
            cur = r
        return True, "ok", all_h, all_l, tot

    def ip_of(self, dev):
        for _p, _i, ipi in self.ifaces(dev):
            if ipi:
                return ipi
        return None


class FakeShell(Terminal):
    """Satr tahrirlovchi soxta terminal (demo)."""

    def __init__(self, driver, name, shell):
        super().__init__()
        self.drv = driver
        self.name = name
        self.shell = shell
        self.buf = ""
        self.esc = 0
        self.busy = None
        self.history = []

    @property
    def prompt(self):
        t = self.drv.project["devices"][self.name]["type"]
        if t == "router" and self.shell in ("default", "vtysh"):
            return f"{self.name}# "
        if t == "p4switch" and self.shell in ("default", "cli"):
            return "RuntimeCmd: "
        return f"\x1b[1;36m{self.name}\x1b[0m:~# "

    def out(self, s):
        self.on_output(s.replace("\n", "\r\n").encode())

    def start(self):
        t = self.drv.project["devices"][self.name]["type"]
        self.out(f"\x1b[2mNetLab demo terminali — {self.name} ({DEVICE_TYPES[t]['label']}). "
                 f"'help' — buyruqlar.\x1b[0m\n")
        self.out(self.prompt)

    def write(self, data):
        for ch in data.decode(errors="ignore"):
            if self.esc:
                self.esc = 0 if (ch.isalpha() or ch == "~") else self.esc
                continue
            if ch == "\x1b":
                self.esc = 1
                continue
            if ch == "\x03":
                if self.busy:
                    self.busy.set()
                self.buf = ""
                self.out("^C\n" + ("" if self.busy else self.prompt))
                continue
            if self.busy:
                continue
            if ch in ("\r", "\n"):
                line = self.buf.strip()
                self.buf = ""
                self.out("\n")
                if line:
                    self.history.append(line)
                    self.run(line)
                else:
                    self.out(self.prompt)
            elif ch in ("\x7f", "\b"):
                if self.buf:
                    self.buf = self.buf[:-1]
                    self.out("\b \b")
            elif ch == "\x0c":
                self.out("\x1b[2J\x1b[H" + self.prompt + self.buf)
            elif ch >= " ":
                self.buf += ch
                self.out(ch)

    def run(self, line):
        try:
            res = self.drv.shell_exec(self, self.name, self.shell, line)
        except Exception as e:  # noqa: BLE001
            res = f"xato: {e}\n"
        if res is None:      # asinxron buyruq (ping) — o'zi prompt chiqaradi
            return
        self.out(res + self.prompt)

    def finish(self):
        self.busy = None
        self.out(self.prompt)


class DemoDriver(BaseDriver):
    name = "demo"

    def __init__(self, bus):
        super().__init__(bus)
        self._stop = threading.Event()
        self._traffic = {}   # link_id -> [ab_bytes, ba_bytes] oxirgi sekundda
        self._arp = {}       # dev -> {ip: (mac, ifname)}
        self._fdb = {}       # switch -> {mac: port}

    def capabilities(self):
        return {"root": False, "mininet": False, "ovs": False, "frr": False, "p4": False,
                "p4c": False, "sdn": False, "tcpdump": False, "demo": True}

    # ---- start/stop ----
    def start(self, project):
        with self.lock:
            if self.running:
                raise DriverError("Tarmoq allaqachon ishlayapti")
            errors = validate_project(project)
            if errors:
                raise DriverError("; ".join(errors[:5]))
            self._set_project(project)
            self.started_at = time.time()
            self._arp, self._fdb, self._traffic = {}, {}, {}
            self._stop.clear()
            threading.Thread(target=self._bg_loop, daemon=True).start()
        self.bus.log("ok", f"[demo] Tarmoq ishga tushdi: {len(project['devices'])} qurilma, "
                           f"{len(project['links'])} kabel")
        self._publish_state()
        return self.status()

    def stop(self):
        with self.lock:
            self._stop.set()
            self.project = None
            self.started_at = None
        self.bus.log("info", "[demo] Tarmoq to'xtatildi")
        self._publish_state()
        return self.status()

    # ---- jonli o'zgarishlar ----
    def _sync(self, project):
        with self.lock:
            self._require_running()
            self._set_project(project)
            return {"ok": True}

    def add_device(self, project, name):
        r = self._sync(project)
        self.bus.log("ok", f"{name} qo'shildi", name)
        return r

    def remove_device(self, name):
        with self.lock:
            self._require_running()
            for port, (lid, _p, _pp) in list(link_ports(self.project, name).items()):
                self.project["links"].pop(lid, None)
            self.project["devices"].pop(name, None)
        return {"ok": True}

    def add_link(self, project, link_id):
        return self._sync(project)

    def remove_link(self, link_id):
        with self.lock:
            self._require_running()
            self.project["links"].pop(link_id, None)
        return {"ok": True}

    def update_link(self, link_id, params=None, up=None):
        with self.lock:
            self._require_running()
            l = self.project["links"].get(link_id)
            if not l:
                raise DriverError(f"kabel {link_id} topilmadi")
            if params is not None:
                l["params"] = normalize_link_params(params)
            if up is not None:
                l["up"] = bool(up)
        self.bus.publish({"type": "link", "link": link_id, "up": l.get("up", True), "params": l.get("params")})
        return {"ok": True, "link": l}

    def apply_device_config(self, project, name):
        r = self._sync(project)
        self.bus.log("ok", f"{name}: sozlama qo'llandi", name)
        return r

    # ---- inspect ----
    def inspect(self, name):
        with self.lock:
            self._require_running()
            d = self.project["devices"].get(name)
            if not d:
                raise DriverError(f"{name} yo'q")
            n = Net(self.project)
            t = d["type"]
            secs = []
            ports = link_ports(self.project, name)
            if DEVICE_TYPES[t]["l3"]:
                rows = []
                for port, ifn, ipi in n.ifaces(name):
                    lid = ports[port][0]
                    up = self.project["links"][lid].get("up") is not False
                    rows.append([ifn, "UP" if up else "DOWN", device_mac(d["id"], port),
                                 str(ipi) if ipi else "—", 1500, "-", "-", 0])
                secs.append({"title": "Interfeyslar", "kind": "table",
                             "columns": ["Interfeys", "Holat", "MAC", "IPv4", "MTU", "RX pkt", "TX pkt", "Drop"],
                             "rows": rows})
                cfg = d.get("config") or {}
                if "isisd" in (cfg.get("daemons") or []):
                    from lab.isis import neighbors
                    secs.append({"title": "IS-IS neighbors (model)", "kind": "table",
                                 "columns": ["Neighbor", "Interface", "State", "Level"],
                                 "rows": [[r[k] for k in ("neighbor", "interface", "state", "level")]
                                          for r in neighbors(n, name)]})
                if cfg.get("dhcp_server"):
                    from lab.services import resolve_dhcp
                    rows = [[x["mac"], x["ip"], x["client"], x["server_iface"]]
                            for x in resolve_dhcp(self.project)[1] if x["server"] == name]
                    secs.append({"title": "DHCP lizinglari", "kind": "table",
                                 "columns": ["MAC", "IP", "Mijoz", "Interfeys"], "rows": rows})
                if cfg.get("nat"):
                    secs.append({"title": "NAT", "kind": "text",
                                 "text": f"MASQUERADE: ichki interfeyslar -> {cfg['nat'].get('outside')} "
                                         f"({next((str(i.ip) for _p, f, i in n.ifaces(name) if f == cfg['nat'].get('outside') and i), '?')})"})
                if "pimd" in (cfg.get("daemons") or []):
                    from lab.mcast import demo_section
                    secs.append(demo_section(n, name))
                if cfg.get("firewall"):
                    from lab.firewall import chain_policy
                    fw = cfg["firewall"]
                    rows = [[i + 1, r.get("chain", "forward"), r.get("action"), r.get("proto") or "any",
                             r.get("src") or "*", r.get("dst") or "*", r.get("dport") or "", r.get("in") or "*",
                             r.get("out") or "*", r.get("state") or ""] for i, r in enumerate(fw.get("rules") or [])]
                    rows += [["siyosat", ch, chain_policy(d, ch), "", "", "", "", "", "", ""]
                             for ch in ("input", "forward", "output")]
                    secs.append({"title": "Firewall (ACL)", "kind": "table",
                                 "columns": ["#", "Zanjir", "Amal", "Proto", "Manba", "Manzil", "dport", "in", "out",
                                             "Holat"], "rows": rows})
                rt = n.routes(name)
                secs.append({"title": "Routing jadvali", "kind": "table",
                             "columns": ["Manzil", "Gateway", "Interfeys", "Protokol", "Metric"],
                             "rows": [[str(a), str(b) if b else "—", c, p, ""] for a, b, c, p in rt]})
                arp = self._arp.get(name, {})
                secs.append({"title": "ARP jadvali", "kind": "table",
                             "columns": ["IP", "MAC", "Interfeys", "Holat"],
                             "rows": [[ip, m, i, "REACHABLE"] for ip, (m, i) in arp.items()]})
                if t == "router":
                    secs.append({"title": "FRR: show ip route", "kind": "text", "text": self._show_ip_route(n, name)})
                    if (d.get("config") or {}).get("vrrp"):
                        from lab.vrrp import model_rows
                        secs.append({"title": "VRRP (show vrrp)", "kind": "table",
                                     "columns": ["VRID", "Interfeys", "VIP", "Prioritet", "Holat", "Master"],
                                     "rows": model_rows(n.p, name)})
                    rows_b, sess = n.bgp()
                    if name in sess:
                        secs.append({"title": "FRR: show bgp summary", "kind": "table",
                                     "columns": ["Qo'shni", "AS", "Tur", "Holat", "PfxRcd"],
                                     "rows": [[x["neighbor"], x["remote_as"], x["type"], x["state"],
                                               x.get("prefixes", 0) if x["state"] == "Established" else "—"]
                                              for x in sess[name]]})
                        secs.append({"title": "FRR: show ip bgp", "kind": "table",
                                     "columns": ["Prefiks", "Next hop", "Interfeys", "AS_PATH / tur"],
                                     "rows": [[str(r[0]), str(r[1]), r[2], r[3]] for r in rows_b.get(name, [])]})
            if t == "wifiap":
                rows = []
                for p_, (lid_, peer_, _pp) in sorted(ports.items()):
                    e_ = wifi_link_estimate(self.project, lid_)
                    if e_:
                        rows.append([lid_, peer_, e_["distance_m"], e_["rssi_dbm"], e_["snr_db"],
                                     e_["mcs"] if e_["ok"] else "—", e_["thr_mbps"]])
                secs.append({"title": "Radio (model): stansiyalar", "kind": "table",
                             "columns": ["Kabel", "Stansiya", "Masofa m", "RSSI dBm", "SNR dB", "MCS", "Taxminiy Mbit/s"],
                             "rows": rows})
            if t in ("switch", "p4switch", "sdnrouter", "wifiap"):
                rows = [[p, ifname(name, p), f"{peer}:{pp}", "-", "-", "-", "-", 0]
                        for p, (lid, peer, pp) in sorted(ports.items())]
                secs.append({"title": "Portlar", "kind": "table",
                             "columns": ["Port", "Interfeys", "Ulangan", "RX pkt", "TX pkt", "RX bayt", "TX bayt", "Drop"],
                             "rows": rows})
            if t == "switch":
                fdb = self._fdb.get(name, {})
                secs.append({"title": "MAC jadvali (FDB)", "kind": "table",
                             "columns": ["Port", "VLAN", "MAC", "Yosh (s)"],
                             "rows": [[p, 0, m, int(time.time() - ts)] for m, (p, ts) in fdb.items()]})
                secs.append({"title": "OpenFlow jadvallari", "kind": "text",
                             "text": " cookie=0x0, table=0, priority=0 actions=NORMAL\n(demo rejimi)"})
                from lab.stp import inspect_section
                sec = inspect_section(self.project, name)
                if sec:
                    secs.append(sec)
                from lab.lag import inspect_section as lag_section
                sec = lag_section(self.project, name, n.link_ok)
                if sec:
                    secs.append(sec)
            if t == "p4switch":
                prog = (d.get("config") or {}).get("program")
                src = (self.project.get("p4_programs") or {}).get(prog, "")
                secs.insert(0, {"title": "P4 pipeline (manbadan, demo)", "kind": "pipeline",
                                "data": parse_p4_outline(src)})
            if t == "sdnrouter":
                secs.append({"title": "Kontroller", "kind": "text",
                             "text": f"kontroller: {(d.get('config') or {}).get('controller')}\n(demo rejimida kontroller kodi ishlamaydi — routing ulangan+statik marshrutlar bo'yicha simulyatsiya qilinadi)"})
            return {"device": name, "type": t, "sections": secs, "demo": True}

    def _show_ip_route(self, n, name):
        lines = ["Codes: K - kernel, C - connected, S - static, O - OSPF, B - BGP, R - RIP, I - IS-IS", ""]
        code = {"connected": "C>*", "static": "S>*"}
        for net, via, ifn, proto in n.routes(name):
            c = code.get(proto, proto[0] + ">*")
            if via:
                lines.append(f"{c} {net} via {via}, {ifn}")
            else:
                lines.append(f"{c} {net} is directly connected, {ifn}")
        return "\n".join(lines)

    # ---- terminal ----
    def open_terminal(self, name, shell="default"):
        with self.lock:
            self._require_running()
            if name not in self.project["devices"]:
                raise DriverError(f"{name} yo'q")
        return FakeShell(self, name, shell)

    def shell_exec(self, sh, name, shell, line):
        with self.lock:
            if not self.project or name not in self.project["devices"]:
                return "qurilma o'chirilgan\n"
            family = 6 if any(x in ("-6", "ipv6", "ping6") or ":" in x for x in line.split()) else 4
            n = Net(copy.deepcopy(self.project), ip_version=family)
        line = line.replace("ip -6 ", "ip ").replace("show ipv6 route", "show ip route")
        if line.startswith("ping6 "):
            line = "ping " + line[6:]
        d = n.devs[name]
        t = d["type"]
        argv = line.split()
        cmd = argv[0]
        if cmd in ("help", "?"):
            return ("Buyruqlar (demo): ip a | ip r | arp | ping <ip> [-c N] | traceroute <ip> | "
                    "show ip route | show interface brief | flows | fdb | clear | exit\n"
                    "Haqiqiy CLI (bash/vtysh/simple_switch_CLI) real driverda ishlaydi.\n")
        if cmd == "clear":
            return "\x1b[2J\x1b[H"
        if cmd == "exit":
            sh.on_exit()
            return ""
        if line in ("ip a", "ip addr", "ifconfig", "show interface brief", "show int brief"):
            rows = []
            for port, ifn, ipi in n.ifaces(name):
                rows.append(f"{ifn:<14} {str(ipi) if ipi else 'unassigned':<20} {device_mac(d['id'], port)}")
            return ("\n".join(rows) or "(interfeys yo'q — kabel ulang)") + "\n"
        if line in ("ip r", "ip route", "route -n"):
            out = []
            for net, via, ifn, proto in n.routes(name):
                out.append(f"{net}{' via ' + str(via) if via else ''} dev {ifn} proto {proto}")
            return ("\n".join(out) or "(bo'sh)") + "\n"
        if line.startswith("show isis neighbor"):
            from lab.isis import neighbors
            return "Neighbor  Interface  State  Level (model)\n" + "\n".join(
                "  ".join(r[k] for k in ("neighbor", "interface", "state", "level")) for r in neighbors(n, name)) + "\n"
        if line.startswith("show ip route"):
            return self._show_ip_route(n, name) + "\n"
        if line.startswith("show vrrp"):
            from lab.vrrp import model_rows
            rows = model_rows(n.p, name)
            if not rows:
                return "% VRRP sozlanmagan\n"
            return " VRID  Interface      VIP                Priority  State       Master\n" + "".join(
                f" {r[0]:<5} {r[1]:<14} {r[2]:<18} {r[3]:<9} {r[4]:<11} {r[5]}\n" for r in rows)
        if line.startswith(("show bgp summary", "show ip bgp summary", "show ip bgp", "show bgp")):
            rows_b, sess = n.bgp()
            if name not in sess:
                return "% BGP sozlanmagan\n"
            if "summary" in line:
                return "Neighbor        V   AS   State/PfxRcd\n" + "".join(
                    f"{x['neighbor']:<15} 4 {x['remote_as']:>6}   "
                    f"{x.get('prefixes', 0) if x['state'] == 'Established' else x['state']}\n" for x in sess[name])
            return "   Network          Next Hop         Path\n" + "".join(
                f"*> {str(r[0]):<16} {str(r[1]):<16} {r[3]}\n" for r in rows_b.get(name, []))
        if cmd in ("arp", "ip n", "ip neigh") or line in ("ip n", "ip neigh"):
            arp = self._arp.get(name, {})
            return ("\n".join(f"{ip:<16} {m}  {i}" for ip, (m, i) in arp.items()) or "(bo'sh)") + "\n"
        if cmd == "fdb" and t == "switch":
            fdb = self._fdb.get(name, {})
            return " port  MAC\n" + "\n".join(f" {p:<5} {m}" for m, (p, _ts) in fdb.items()) + "\n"
        if cmd == "flows":
            return " priority=0 actions=NORMAL (demo)\n"
        if line.startswith(("configure", "conf t", "router ", "interface ")):
            return "% demo rejimida konfiguratsiya UI'dagi 'Sozlama' bo'limi orqali qilinadi\n"
        if cmd in ("ping", "traceroute", "tracert"):
            if not DEVICE_TYPES[t]["l3"]:
                return f"{name}: L2 qurilmada IP yo'q\n"
            target = next((a for a in argv[1:] if not a.startswith("-") and not a.isdigit()), None)
            count = 4
            if "-c" in argv:
                try:
                    count = max(1, min(int(argv[argv.index("-c") + 1]), 50))
                except (ValueError, IndexError):
                    pass
            try:
                dst = ipaddress.ip_address(target)
            except (TypeError, ValueError):
                # nom bo'yicha (masalan 'ping pc2')
                if target in n.devs and n.ip_of(target):
                    dst = n.ip_of(target).ip
                else:
                    return f"{cmd}: '{target}' noto'g'ri manzil\n"
            stop = threading.Event()
            sh.busy = stop
            fn = self._ping_job if cmd == "ping" else self._trace_job
            threading.Thread(target=fn, args=(sh, name, dst, count, stop), daemon=True).start()
            return None
        return f"{cmd}: buyruq topilmadi (demo). 'help' ni yozing\n"

    def _learn(self, n, src_dev, links):
        """ARP/FDB jadvallarini yo'l bo'yicha to'ldirish (inspect uchun)."""
        now = time.time()
        for lid, direction in links:
            l = n.p["links"].get(lid)
            if not l:
                continue
            a, b = (l["a"], l["b"]) if direction == "ab" else (l["b"], l["a"])
            if n.devs.get(b["dev"], {}).get("type") == "switch":
                mac = device_mac(n.devs[src_dev]["id"], 1)
                self._fdb.setdefault(b["dev"], {})[mac] = (b["port"], now)

    def _emit_path(self, links, proto, summary, start_delay=0.0):
        """Paket hodisalarini yo'l bo'yicha ketma-ket yuborish (animatsiya)."""
        t = start_delay
        with self.lock:
            proj = self.project
        if not proj:
            return
        for lid, direction in links:
            l = proj["links"].get(lid)
            if not l:
                continue
            dl = max(_ms((l.get("params") or {}).get("delay", "1ms")), 1.0)
            threading.Timer(t, self._pkt, args=(lid, direction, proto, summary)).start()
            t += 0.18 + dl / 1000.0
        return t

    def _pkt(self, lid, direction, proto, summary, size=98):
        if self.capture_on and self.project:
            self.bus.publish({"type": "packet", "link": lid, "dir": direction, "proto": proto,
                              "summary": summary})
        tr = self._traffic.setdefault(lid, [0, 0])
        tr[0 if direction == "ab" else 1] += size

    def _ping_job(self, sh, name, dst, count, stop):
        sent = recv = 0
        rtts = []
        sh.out(f"PING {dst} 56(84) bytes of data.\n")
        for seq in range(1, count + 1):
            if stop.is_set() or not self.project:
                break
            with self.lock:
                if not self.project:
                    break
                n = Net(copy.deepcopy(self.project), ip_version=dst.version)
            sent += 1
            src_ip = n.ip_of(name)
            ok, why, hops, links, d1 = n.forward(name, dst)
            if ok and src_ip:
                from lab.firewall import check_roundtrip
                dst_dev, _ = n.owner_of(dst)
                okf, whyf, verdict = check_roundtrip(n, name, src_ip.ip, dst_dev, dst, hops,
                                                     {"proto": "icmp", "src": str(src_ip.ip), "dst": str(dst)})
                if not okf:
                    self._emit_path(links, "ICMP", f"IP {src_ip.ip} > {dst}: ICMP echo request, seq {seq}")
                    time.sleep(1.0)
                    if verdict == "reject":
                        sh.out(f"From {whyf.split(':')[0]} icmp_seq={seq} Destination Host Prohibited ({whyf})\n")
                    else:
                        sh.out(f"Request timeout for icmp_seq {seq} ({whyf})\n")
                    if seq < count:
                        stop.wait(0.4)
                    continue
            if ok:
                dst_dev, _ = n.owner_of(dst)
                ok2, why2, hops2, back, d2 = n.reply(name, src_ip.ip if src_ip else dst, dst_dev, dst, hops)
                t = self._emit_path(links, "ICMP", f"IP {src_ip.ip if src_ip else '?'} > {dst}: ICMP echo request, seq {seq}")
                if ok2:
                    self._emit_path(back, "ICMP", f"IP {dst} > {src_ip.ip}: ICMP echo reply, seq {seq}", t or 0)
                    rtt = d1 + d2 + random.uniform(0.05, 0.4) * (len(links) + len(back))
                    rtts.append(rtt)
                    recv += 1
                    self._learn(n, name, links)
                    self._arp.setdefault(name, {})
                    if hops:
                        nh_dev, nh_ip = hops[0]
                        self._arp[name][nh_ip] = (device_mac(n.devs[nh_dev]["id"], 1), "")
                    time.sleep(min((t or 0.3) + 0.2, 1.5))
                    sh.out(f"64 bytes from {dst}: icmp_seq={seq} ttl={64 - max(len(hops) - 1, 0)} time={rtt:.2f} ms\n")
                else:
                    time.sleep(1.0)
                    sh.out(f"Request timeout for icmp_seq {seq} (qaytish yo'li: {why2})\n")
            else:
                self._emit_path(links, "ICMP", f"IP > {dst}: ICMP echo request, seq {seq}")
                time.sleep(1.0)
                sh.out(f"From {src_ip.ip if src_ip else name} icmp_seq={seq} {why}\n")
            if seq < count:
                stop.wait(0.4)
        loss = 100 * (sent - recv) / sent if sent else 0
        sh.out(f"\n--- {dst} ping statistics ---\n{sent} packets transmitted, {recv} received, {loss:.0f}% packet loss\n")
        if rtts:
            avg = sum(rtts) / len(rtts)
            sh.out(f"rtt min/avg/max = {min(rtts):.2f}/{avg:.2f}/{max(rtts):.2f} ms\n")
        sh.finish()

    def _trace_job(self, sh, name, dst, _count, stop):
        with self.lock:
            n = Net(copy.deepcopy(self.project), ip_version=dst.version)
        ok, why, hops, links, delay = n.forward(name, dst)
        sh.out(f"traceroute to {dst}, 16 hops max\n")
        self._emit_path(links, "UDP", f"UDP traceroute -> {dst}")
        acc = 0.0
        for i, (dev, ip) in enumerate(hops, 1):
            if stop.is_set():
                break
            acc += random.uniform(0.3, 1.2)
            time.sleep(0.35)
            sh.out(f" {i:>2}  {ip} ({dev})  {acc:.2f} ms\n")
        if not ok:
            sh.out(f" *  {why}\n")
        sh.finish()

    # ---- fon: routing protokol hello'lari va statistika ----
    def _bg_loop(self):
        tick = 0
        while not self._stop.wait(1.0):
            tick += 1
            with self.lock:
                if not self.project:
                    continue
                proj = self.project
                n = Net(copy.deepcopy(proj))
            if tick % 5 == 0:
                for name in n.devs:
                    mine = n.dyn_daemons(name)
                    if not mine:
                        continue
                    proto = DYNAMIC[sorted(mine)[0]]
                    for port, ifn, ipi, nd, nip, path in n.adjacency(name):
                        if n.dyn_daemons(nd) & mine:
                            self._emit_path(path, proto.split("v")[0] if proto.startswith("OSPF") else proto,
                                            f"IP {ipi.ip} > 224.0.0.5: {proto} Hello")
            stp = [nm for nm, d in n.devs.items() if d["type"] == "switch" and (d.get("config") or {}).get("stp")]
            if tick % 2 == 0:
                for nm in stp:
                    for p, (lid, peer, pp) in link_ports(proj, nm).items():
                        l = proj["links"].get(lid)
                        if l and l.get("up") is not False:
                            self._pkt(lid, "ab" if l["a"]["dev"] == nm else "ba", "STP", "STP 802.1d, Config BPDU", 64)
            out = {}
            for lid, l in proj["links"].items():
                ab, ba = self._traffic.get(lid, [0, 0])
                bw = float((l.get("params") or {}).get("bw", 100)) * 1e6
                out[lid] = {"ab_bps": ab * 8, "ba_bps": ba * 8, "ab_pps": round(ab / 98, 1),
                            "ba_pps": round(ba / 98, 1), "util": round(min(max(ab, ba) * 8 / bw, 1), 5), "drops": 0}
            self._traffic = {}
            self.bus.publish({"type": "stats", "links": out})

    # ---- P4 / SDN / FRR (demo) ----
    def p4_load(self, project, name):
        self._sync(project)
        d = self.project["devices"][name]
        prog = (d.get("config") or {}).get("program")
        src = (self.project.get("p4_programs") or {}).get(prog, "")
        outline = parse_p4_outline(src)
        n_tables = sum(len(p["tables"]) for p in outline["pipelines"])
        return {"ok": True, "log": f"[demo] p4c mavjud emas — manba tahlil qilindi: "
                                   f"{len(outline['parsers'][0]['states']) if outline['parsers'] else 0} parser holati, "
                                   f"{n_tables} jadval. Haqiqiy kompilyatsiya real driverda.",
                "pipeline": outline}

    def p4_runtime(self, name, commands):
        lines = [l for l in commands.splitlines() if l.strip()]
        return {"ok": True, "output": "\n".join(f"RuntimeCmd: {l}\n[demo] qabul qilindi" for l in lines)}

    def controller_restart(self, project, cname):
        self._sync(project)
        return {"ok": True, "log": f"[demo] '{cname}' kontroller kodi saqlandi. Haqiqiy os-ken real driverda ishlaydi."}

    def controller_log(self, cname):
        return "[demo] kontroller logi faqat real driverda"

    def frr_running_config(self, name):
        with self.lock:
            self._require_running()
            d = self.project["devices"][name]
            n = Net(self.project)
            cfg = d.get("config") or {}
            lines = [f"hostname {name}", "!"]
            for _p, ifn, ipi in n.ifaces(name):
                lines += [f"interface {ifn}"] + ([f" ip address {ipi}"] if ipi else []) + ["!"]
            for r in cfg.get("routes") or []:
                lines.append(f"ip route {r['prefix']} {r['via']}")
            if "ospfd" in cfg.get("daemons", []):
                lines += ["router ospf"] + [f" network {ipi.network} area 0" for _p, _i, ipi in n.ifaces(name) if ipi] + ["!"]
            return (cfg.get("startup") or "\n".join(lines)).strip()


def parse_p4_outline(src):
    """P4_16 manbadan taxminiy pipeline tuzilmasi (kompilyatorsiz, demo uchun)."""
    out = {"parsers": [], "pipelines": [], "deparsers": [], "headers": [], "counters": [], "registers": []}
    if not src:
        return out
    clean = re.sub(r"//[^\n]*|/\*.*?\*/", "", src, flags=re.S)
    out["headers"] = re.findall(r"\bheader\s+(\w+)", clean)
    states = []
    for m in re.finditer(r"\bstate\s+(\w+)\s*\{(.*?)\n\s*\}", clean, re.S):
        body = m.group(2)
        ext = re.findall(r"extract\s*\(\s*([\w.]+)", body)
        nxt = re.findall(r"(?:transition\s+|:\s*)(\w+)\s*;", body)
        states.append({"name": m.group(1), "extracts": ext, "next": sorted(set(nxt)) or ["accept"]})
    if states:
        out["parsers"].append({"name": "parser", "states": states})
    for cm in re.finditer(r"\bcontrol\s+(\w+)\s*\(", clean):
        start = cm.end()
        nxt = re.search(r"\bcontrol\s+\w+\s*\(|\bV1Switch\b", clean[start:])
        body = clean[start: start + (nxt.start() if nxt else len(clean))]
        tables = []
        for tm in re.finditer(r"\btable\s+(\w+)\s*\{(.*?)\n\s*\}", body, re.S):
            tb = tm.group(2)
            keys = re.search(r"key\s*=\s*\{(.*?)\}", tb, re.S)
            acts = re.search(r"actions\s*=\s*\{(.*?)\}", tb, re.S)
            size = re.search(r"size\s*=\s*(\d+)", tb)
            tables.append({
                "name": tm.group(1),
                "match": [{"field": f, "type": t} for f, t in
                          re.findall(r"([\w.\[\]]+)\s*:\s*(\w+)\s*;", keys.group(1) if keys else "")],
                "actions": [a.strip().rstrip(";").split("(")[0] for a in (acts.group(1) if acts else "").split(";") if a.strip()],
                "size": int(size.group(1)) if size else None,
            })
        if tables:
            out["pipelines"].append({"name": cm.group(1), "tables": tables})
        if "emit" in body:
            out["deparsers"].append({"name": cm.group(1), "order": re.findall(r"emit\s*\(\s*([\w.]+)", body)})
        out["counters"] += re.findall(r"\bcounter\s*\([^)]*\)\s*(\w+)", body)
        out["registers"] += [{"name": r, "size": None, "bitwidth": None}
                             for r in re.findall(r"\bregister\s*<[^;(]*>\s*\([^)]*\)\s*(\w+)", body)]
    return out
