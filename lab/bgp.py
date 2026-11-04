"""BGP modeli (demo / analitik / ns-3 uchun) — path-vector, FRR xatti-harakatiga yaqin.

Router `config.bgp`:
    {"asn": 65001, "router_id": "1.1.1.1",
     "neighbors": [{"ip": "203.0.113.2", "remote_as": 65002, "next_hop_self": false}],
     "networks": ["10.1.0.0/24"], "redistribute": ["connected", "ospf", "rip", "static"]}
`daemons` ichida "bgpd" bo'lishi kerak.

Qoidalar:
  * sessiya UP: ikkala tomon bir-birini (IP + remote_as) ko'rsatgan; eBGP — qo'shni IP
    to'g'ridan-to'g'ri ulangan subnetda (L2 orqali yetadi); iBGP — IGP/statik marshrut bilan yetadi.
  * e'lon: `networks` (FRR import-check: prefiks RIB'da bo'lsa) + redistribute.
  * eBGP: AS_PATH oldiga o'z AS'i, next-hop = o'zi; qabul qiluvchi AS_PATH'da o'z AS'ini ko'rsa rad etadi.
  * iBGP: faqat lokal/eBGP'dan o'rganilganlar uzatiladi (route reflector yo'q), next-hop
    o'zgarmaydi (`next_hop_self` bo'lmasa) — qabul qiluvchi uni IGP orqali rekursiv hal qiladi.
  * tanlash: lokal > qisqa AS_PATH > eBGP > iBGP > kichik qo'shni IP.
  * AD: eBGP 20, iBGP 200 (connected 0, static 1, OSPF 110, RIP 120).
"""

import ipaddress

AD = {"connected": 0, "static": 1, "ebgp": 20, "ospf": 110, "is-is": 115, "rip": 120, "ibgp": 200}


def bgp_cfg(dev):
    d = dev or {}
    cfg = d.get("config") or {}
    if d.get("type") != "router" or "bgpd" not in (cfg.get("daemons") or []):
        return None
    b = cfg.get("bgp") or {}
    try:
        return {**b, "asn": int(b["asn"])} if b.get("asn") else None
    except (TypeError, ValueError):
        return None


def validate_bgp(project):
    errors = []
    for name, d in (project.get("devices") or {}).items():
        b = (d.get("config") or {}).get("bgp")
        if not b:
            continue
        if d.get("type") != "router":
            errors.append(f"{name}: BGP faqat router'da")
            continue
        try:
            if not 1 <= int(b.get("asn")) <= 4294967295:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{name}: BGP asn 1..4294967295")
        if b.get("router_id"):
            try:
                ipaddress.IPv4Address(b["router_id"])
            except ValueError:
                errors.append(f"{name}: BGP router_id IPv4 ko'rinishida (masalan 1.1.1.1)")
        for nb in b.get("neighbors") or []:
            try:
                ipaddress.IPv4Address(nb.get("ip"))
                if not 1 <= int(nb.get("remote_as")) <= 4294967295:
                    raise ValueError
            except (TypeError, ValueError, ipaddress.AddressValueError):
                errors.append(f"{name}: BGP qo'shni {nb} noto'g'ri (ip, remote_as)")
        for n in b.get("networks") or []:
            try:
                ipaddress.ip_network(n, strict=False)
            except ValueError:
                errors.append(f"{name}: BGP network '{n}' noto'g'ri")
        for r in b.get("redistribute") or []:
            if r not in ("connected", "ospf", "rip", "static", "isis"):
                errors.append(f"{name}: BGP redistribute '{r}' (connected|ospf|rip|static|isis)")
        if "bgpd" not in ((d.get("config") or {}).get("daemons") or []):
            errors.append(f"{name}: BGP sozlangan, lekin daemons ichida 'bgpd' yo'q")
    return errors


def _base_lookup(base, ip):
    best = None
    for r in base:
        if ip in r[0] and (best is None or r[0].prefixlen > best[0].prefixlen):
            best = r
    return best


def compute(net):
    """-> ({router: [(network, via, ifname, proto)]}, {router: [session dict]})."""
    devs = net.devs
    spk = {n: bgp_cfg(d) for n, d in devs.items()}
    spk = {n: b for n, b in spk.items() if b}
    if not spk:
        return {}, {}
    base = {n: net._base_routes(n) for n in spk}
    my_ips = {n: {str(ipi.ip): ifn for _p, ifn, ipi in net.ifaces(n) if ipi} for n in spk}
    adj = {n: {str(nip.ip): (nd, ifn) for _p, ifn, _m, nd, nip, _path in net.adjacency(n)} for n in spk}

    sessions = {n: [] for n in spk}
    for a, b in spk.items():
        for nb in b.get("neighbors") or []:
            ip = str(nb.get("ip"))
            owner, _port = net.owner_of(ipaddress.ip_address(ip))
            st = {"neighbor": ip, "remote_as": int(nb.get("remote_as", 0)), "peer": owner, "state": "Active",
                  "type": "ebgp" if int(nb.get("remote_as", 0)) != b["asn"] else "ibgp",
                  "next_hop_self": bool(nb.get("next_hop_self")), "local_ip": None}
            pb = spk.get(owner)
            if pb and pb["asn"] == st["remote_as"]:
                back = [x for x in pb.get("neighbors") or [] if str(x.get("ip")) in my_ips[a]
                        and int(x.get("remote_as", 0)) == b["asn"]]
                if back:
                    st["local_ip"] = str(back[0]["ip"])
                    if st["type"] == "ebgp":
                        ok = ip in adj[a]
                    else:
                        ok = ip in adj[a] or _base_lookup(base[a], ipaddress.ip_address(ip)) is not None
                    if ok:
                        st["state"] = "Established"
            sessions[a].append(st)

    # lokal e'lonlar
    rib = {n: {} for n in spk}   # prefix -> (as_path, next_hop|None, src: local|ebgp|ibgp, from_ip)
    for n, b in spk.items():
        redis = set(b.get("redistribute") or [])
        nets = set()
        for x in b.get("networks") or []:
            pn = ipaddress.ip_network(x, strict=False)
            if any(r[0] == pn for r in base[n]):        # import-check
                nets.add(pn)
        for netw, _via, _ifn, proto in base[n]:
            p = proto.split(" ")[0].lower()
            if (p == "connected" and "connected" in redis) or (p == "static" and "static" in redis) or \
               (p == "ospf" and "ospf" in redis) or (p == "rip" and "rip" in redis) or \
               (p == "is-is" and "isis" in redis):
                if netw.prefixlen > 0:
                    nets.add(netw)
        for pn in nets:
            rib[n][pn] = ((), None, "local", None)

    def better(new, old):
        if old is None:
            return True
        if old[2] == "local":
            return False
        if new[2] == "local":
            return True
        if len(new[0]) != len(old[0]):
            return len(new[0]) < len(old[0])
        if new[2] != old[2]:
            return new[2] == "ebgp"
        return ipaddress.ip_address(new[3]) < ipaddress.ip_address(old[3])

    for _ in range(32):
        changed = False
        new_rib = {n: dict(r) for n, r in rib.items()}
        # Adj-RIB-In qayta hisoblanadi: lokal bo'lmaganlarni qayta tanlaymiz
        for n in new_rib:
            new_rib[n] = {p: v for p, v in rib[n].items() if v[2] == "local"}
        for a in spk:
            for st in sessions[a]:
                if st["state"] != "Established":
                    continue
                b_ = st["peer"]
                for pfx, (path, nh, src, _frm) in rib[a].items():
                    if st["type"] == "ebgp":
                        if spk[b_]["asn"] in path:
                            continue                      # AS loop
                        cand = ((spk[a]["asn"],) + path, st["local_ip"], "ebgp", st["local_ip"])
                    else:
                        if src == "ibgp":
                            continue                      # iBGP split horizon
                        nh2 = st["local_ip"] if (st["next_hop_self"] or src == "local") else nh
                        cand = (path, nh2, "ibgp", st["local_ip"])
                    if better(cand, new_rib[b_].get(pfx)):
                        new_rib[b_][pfx] = cand
        if new_rib != rib:
            rib, changed = new_rib, True
        if not changed:
            break

    out = {}
    for n in spk:
        rows = []
        for pfx, (path, nh, src, _frm) in sorted(rib[n].items(), key=lambda kv: str(kv[0])):
            if src == "local" or nh is None:
                continue
            nhip = ipaddress.ip_address(nh)
            r = _base_lookup(base[n], nhip)
            if not r:
                continue                                  # next-hop hal qilinmadi
            via, ifn = (nhip, r[2]) if r[1] is None else (r[1], r[2])   # rekursiv
            rows.append((pfx, via, ifn, f"BGP {'e' if src == 'ebgp' else 'i'} [{' '.join(map(str, path)) or 'i'}]"))
        out[n] = rows
    for n, b in spk.items():
        for st in sessions[n]:
            st["prefixes"] = sum(1 for v in rib[n].values() if v[2] != "local" and v[3] == st["neighbor"])
    return out, sessions


def merge_routes(base, bgp_rows):
    """Base (connected/static/IGP) + BGP: AD bo'yicha bir prefiks uchun bitta marshrut."""
    def ad(proto):
        p = proto.split(" ")[0].lower()
        if p == "bgp":
            return AD["ebgp"] if proto.startswith("BGP e") else AD["ibgp"]
        return AD.get(p, 110)
    best = {}
    order = []
    for r in list(base) + list(bgp_rows):
        k = r[0]
        if k not in best:
            order.append(k)
            best[k] = r
        elif ad(r[3]) < ad(best[k][3]):
            best[k] = r
    return [best[k] for k in order]


def frr_config(b):
    """config.bgp -> FRR konfiguratsiyasi (vtysh -f). eBGP uchun FRR >= 7.4 policy talabini o'chiradi."""
    lines = [f"router bgp {int(b['asn'])}"]
    if b.get("router_id"):
        lines.append(f" bgp router-id {b['router_id']}")
    lines += [" no bgp ebgp-requires-policy", " no bgp default ipv4-unicast"]
    for nb in b.get("neighbors") or []:
        lines.append(f" neighbor {nb['ip']} remote-as {int(nb['remote_as'])}")
        lines.append(f" neighbor {nb['ip']} timers connect 5")
    lines.append(" address-family ipv4 unicast")
    for n in b.get("networks") or []:
        lines.append(f"  network {n}")
    for r in b.get("redistribute") or []:
        lines.append(f"  redistribute {r}")
    for nb in b.get("neighbors") or []:
        lines.append(f"  neighbor {nb['ip']} activate")
        if nb.get("next_hop_self"):
            lines.append(f"  neighbor {nb['ip']} next-hop-self")
    lines += [" exit-address-family", "exit", ""]
    return "\n".join(lines)
