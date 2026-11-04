"""Firewall / ACL modeli (iptables semantikasi) — demo/analitik model va real driver uchun.

Qurilma `config.firewall`:
    {"default": "drop",                         # routerda FORWARD, host/serverda INPUT siyosati
     "policy": {"input": "accept"},             # ixtiyoriy: zanjir bo'yicha aniq siyosat
     "rules": [{"chain": "forward", "action": "accept", "proto": "tcp",
                "src": "0.0.0.0/0", "dst": "10.0.2.10/32", "dport": 80,
                "in": "r1-eth2", "out": "r1-eth3", "state": "new"}, ...]}

Semantika (iptables bilan bir xil): zanjir ichida birinchi mos qoida; mos qoida bo'lmasa
zanjir siyosati. `state`: "new" — yangi ulanish paketi, "established" — javob/davomi
(conntrack ESTABLISHED,RELATED); bo'sh — ikkalasi. `reject`: TCP -> RST, boshqalar ->
ICMP host-prohibited (yuboruvchi darhol xabar oladi), `drop` — jim tashlanadi (timeout).
Model: paket yo'lida manba OUTPUT, oraliq routerlar FORWARD, manzil INPUT zanjiridan
o'tadi; NAT (masquerade) dan keyingi hoplarda manba manzili almashtiriladi; javob
paketlari established sifatida tekshiriladi (stateful).
"""

import ipaddress

CHAINS = ("input", "forward", "output")
ACTIONS = ("accept", "drop", "reject")
PROTOS = ("any", "tcp", "udp", "icmp")
STATES = (None, "", "new", "established")
IPT_CHAIN = {"input": "NETLAB-IN", "forward": "NETLAB-FWD", "output": "NETLAB-OUT"}
IPT_PARENT = {"input": "INPUT", "forward": "FORWARD", "output": "OUTPUT"}


def _fw(dev):
    return ((dev or {}).get("config") or {}).get("firewall") or None


def chain_policy(dev, chain):
    fw = _fw(dev)
    if not fw:
        return "accept"
    pol = (fw.get("policy") or {}).get(chain)
    if pol:
        return pol
    default_chain = "forward" if dev.get("type") == "router" else "input"
    return fw.get("default", "accept") if chain == default_chain else "accept"


def validate_firewall(project):
    errors = []
    for name, d in (project.get("devices") or {}).items():
        fw = _fw(d)
        if not fw:
            continue
        w = f"{name}: firewall"
        if d.get("type") not in ("router", "host", "server"):
            errors.append(f"{w} faqat router/host/server'da")
            continue
        if fw.get("default", "accept") not in ("accept", "drop"):
            errors.append(f"{w}: default accept | drop")
        for ch, pol in (fw.get("policy") or {}).items():
            if ch not in CHAINS or pol not in ("accept", "drop"):
                errors.append(f"{w}: policy {ch}={pol} noto'g'ri (zanjir {CHAINS}, accept | drop)")
        ifs = set(((d.get("config") or {}).get("interfaces") or {}))
        for i, r in enumerate(fw.get("rules") or []):
            rw = f"{w} qoida {i + 1}"
            if r.get("chain", "forward") not in CHAINS:
                errors.append(f"{rw}: chain {CHAINS} dan biri")
            if r.get("action") not in ACTIONS:
                errors.append(f"{rw}: action {ACTIONS} dan biri")
            proto = r.get("proto") or "any"
            if proto not in PROTOS:
                errors.append(f"{rw}: proto {PROTOS} dan biri")
            if r.get("state") not in STATES:
                errors.append(f"{rw}: state new | established")
            for k in ("src", "dst"):
                if r.get(k):
                    try:
                        ipaddress.ip_network(r[k], strict=False)
                    except ValueError:
                        errors.append(f"{rw}: {k} '{r[k]}' noto'g'ri tarmoq")
            for k in ("dport", "sport"):
                if r.get(k) not in (None, ""):
                    try:
                        if not 1 <= int(r[k]) <= 65535:
                            raise ValueError
                    except (TypeError, ValueError):
                        errors.append(f"{rw}: {k} 1..65535")
                    if proto not in ("tcp", "udp"):
                        errors.append(f"{rw}: {k} faqat tcp/udp bilan")
            for k in ("in", "out"):
                if r.get(k) and r[k] not in ifs:
                    errors.append(f"{rw}: interfeys '{r[k]}' {name}'da yo'q")
            if r.get("chain") == "input" and r.get("out") or r.get("chain") == "output" and r.get("in"):
                errors.append(f"{rw}: input zanjirida 'out', output zanjirida 'in' bo'lmaydi")
    return errors


def _match(r, pkt, in_if, out_if, established):
    proto = r.get("proto") or "any"
    if proto != "any" and proto != pkt.get("proto"):
        return False
    for k, ip_k in (("src", "src"), ("dst", "dst")):
        if r.get(k):
            try:
                if ipaddress.ip_address(str(pkt.get(ip_k))) not in ipaddress.ip_network(r[k], strict=False):
                    return False
            except ValueError:
                return False
    for k in ("dport", "sport"):
        if r.get(k) not in (None, "") and int(r[k]) != (pkt.get(k) or -1):
            return False
    if r.get("in") and r["in"] != in_if:
        return False
    if r.get("out") and r["out"] != out_if:
        return False
    st = r.get("state")
    if st == "new" and established or st == "established" and not established:
        return False
    return True


def eval_chain(dev, chain, pkt, in_if=None, out_if=None, established=False):
    """-> (verdict "accept"|"drop"|"reject", qoida indeksi | None)."""
    fw = _fw(dev)
    if not fw:
        return "accept", None
    for i, r in enumerate(fw.get("rules") or []):
        if (r.get("chain") or "forward") == chain and _match(r, pkt, in_if, out_if, established):
            return r["action"], i
    return chain_policy(dev, chain), None


def _iface_with_ip(net, dev, ip):
    return next((ifn for _p, ifn, ipi in net.ifaces(dev) if ipi and str(ipi.ip) == str(ip)), None)


def check_path(net, src_dev, dst, hops, pkt, established=False, end_chain="input", start=True, end_out=None,
               end_dst=None):
    """Net.forward natijasi (hops) bo'yicha paket firewall'lardan o'tadimi.
    -> (ok, verdict, qurilma, zanjir, qoida). ok=True bo'lsa verdict="accept".
    start=False — manba OUTPUT tekshirilmaydi (NAT routerdan davom etayotgan javob);
    end_chain="forward" + end_out — oxirgi qurilma paketni uzatadi (NAT de-masquerade)."""
    pkt = dict(pkt)
    dst = ipaddress.ip_address(str(dst))
    if start:
        r = net.lookup(src_dev, dst)
        v, ri = eval_chain(net.devs[src_dev], "output", pkt, None, r[2] if r else None, established)
        if v != "accept":
            return False, v, src_dev, "output", ri
    for i, (nd, ip_in) in enumerate(hops):
        in_if = _iface_with_ip(net, nd, ip_in)
        last = i == len(hops) - 1
        if last:
            v, ri = eval_chain(net.devs[nd], end_chain, dict(pkt, dst=end_dst) if end_dst else pkt,
                               in_if, end_out, established)
            if v != "accept":
                return False, v, nd, end_chain, ri
            break
        rr = net.lookup(nd, dst)
        out_if = rr[2] if rr else None
        v, ri = eval_chain(net.devs[nd], "forward", pkt, in_if, out_if, established)
        if v != "accept":
            return False, v, nd, "forward", ri
        nat_out = ((net.devs[nd].get("config") or {}).get("nat") or {}).get("outside")
        if nat_out and out_if == nat_out and in_if != nat_out and not established:
            oip = next((ipi.ip for _p, ifn, ipi in net.ifaces(nd) if ifn == nat_out and ipi), None)
            if oip:
                pkt["src"] = str(oip)          # POSTROUTING MASQUERADE — keyingi hoplar uchun
    return True, "accept", None, None, None


def check_roundtrip(net, src_dev, src_ip, dst_dev, dst, fwd_hops, pkt):
    """So'rov (new) + javob (established) — NAT hisobga olingan (Net.reply bilan bir xil yo'l).
    -> (ok, sabab_matni | None, verdict)."""
    ok, v, where, chain, ri = check_path(net, src_dev, dst, fwd_hops, pkt)
    if not ok:
        return False, _why(v, where, chain, ri, pkt), v
    rpkt = {"proto": pkt.get("proto"), "src": str(dst), "dst": str(src_ip),
            "sport": pkt.get("dport"), "dport": pkt.get("sport")}
    chain_nat = net.nat_chain(src_dev, ipaddress.ip_address(str(dst)), fwd_hops)
    legs = [(r, ip) for r, ip in reversed(chain_nat)] + [(src_dev, src_ip)]
    cur = dst_dev
    for k, (r, ip) in enumerate(legs):
        okf, _w, h, _l, _d = net.forward(cur, ipaddress.ip_address(str(ip)))
        if not okf:
            return False, _w, "drop"
        last = k == len(legs) - 1
        # javob dst = NAT outside IP (keyin router uni ichki manzilga qaytaradi — de-NAT)
        leg = dict(rpkt, dst=str(ip))
        nxt = None if last else net.lookup(r, ipaddress.ip_address(str(legs[k + 1][1])))
        okc, v, where, chain, ri = check_path(net, cur, ip, h, leg, established=True, start=(k == 0),
                                             end_chain="input" if last else "forward",
                                             end_out=None if last or not nxt else nxt[2],
                                             end_dst=None if last else str(src_ip))
        if not okc:
            return False, _why(v, where, chain, ri, leg), v
        cur = r
    return True, None, "accept"


def _why(v, where, chain, ri, pkt):
    rule = f"qoida {ri + 1}" if ri is not None else "siyosat"
    if v == "reject":
        msg = "Destination Port Unreachable (tcp reset)" if pkt.get("proto") == "tcp" else "Destination Host Prohibited"
        return f"{where}: {msg} — firewall {chain} {rule}"
    return f"{where}: firewall {chain} {rule} — paket tashlandi (drop)"


# ───────────────────────── iptables (real driver) ─────────────────────────
def iptables_cmds(dev, conntrack=True):
    """Qurilma namespace'i uchun buyruqlar: o'z zanjirlari (NETLAB-IN/FWD/OUT) — nat jadvali
    va boshqa qoidalarga tegilmaydi. Har chaqiruv zanjirlarni tozalab qaytadan yozadi."""
    cmds = []
    for ch in CHAINS:
        c, par = IPT_CHAIN[ch], IPT_PARENT[ch]
        cmds += [f"iptables -N {c} 2>/dev/null || true", f"iptables -F {c}",
                 f"iptables -C {par} -j {c} 2>/dev/null || iptables -I {par} 1 -j {c}"]
    fw = _fw(dev)
    if not fw:
        return cmds
    for r in fw.get("rules") or []:
        ch = r.get("chain") or "forward"
        a = [f"iptables -A {IPT_CHAIN[ch]}"]
        proto = r.get("proto") or "any"
        if proto != "any":
            a.append(f"-p {proto}")
        if r.get("src"):
            a.append(f"-s {r['src']}")
        if r.get("dst"):
            a.append(f"-d {r['dst']}")
        if r.get("in"):
            a.append(f"-i {r['in']}")
        if r.get("out"):
            a.append(f"-o {r['out']}")
        if r.get("sport") not in (None, ""):
            a.append(f"--sport {int(r['sport'])}")
        if r.get("dport") not in (None, ""):
            a.append(f"--dport {int(r['dport'])}")
        if r.get("state") and conntrack:
            a.append("-m conntrack --ctstate " + ("NEW" if r["state"] == "new" else "ESTABLISHED,RELATED"))
        act = r["action"]
        if act == "reject":
            a.append("-j REJECT --reject-with " + ("tcp-reset" if proto == "tcp" else "icmp-host-prohibited"))
        else:
            a.append(f"-j {act.upper()}")
        cmds.append(" ".join(a))
    for ch in CHAINS:
        if chain_policy(dev, ch) == "drop":
            cmds.append(f"iptables -A {IPT_CHAIN[ch]} -j DROP")
    return cmds


def parse_counters(text):
    """`iptables -L NETLAB-X -n -v -x --line-numbers` -> [[#, pkts, bytes, target, prot, in, out, src, dst, extra]]."""
    rows = []
    for line in (text or "").splitlines():
        v = line.split()
        if len(v) >= 10 and v[0].isdigit():
            rows.append([int(v[0]), int(v[1]), int(v[2]), v[3], v[4], v[6], v[7], v[8], v[9] if len(v) > 9 else "",
                         " ".join(v[10:])])
    return rows
