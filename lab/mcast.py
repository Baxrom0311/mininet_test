"""IP multicast: IGMP + PIM-SM modeli (OPNET "multicast" ish oqimi).

Router — `daemons` ichida "pimd" va ixtiyoriy `config.pim`:
    {"rp": "10.0.0.1",                 # Rendezvous Point (FRR: ip pim rp <rp> 224.0.0.0/4)
     "interfaces": ["r1-eth1", ...]}    # PIM/IGMP yoqilgan interfeyslar (bo'lmasa — hammasi)
Switch — `config.igmp_snooping: true` (OVS mcast_snooping): kadrlar faqat a'zo (va
multicast router) portlariga; o'chiq bo'lsa — butun L2 domenga (VLAN/STP hisobga olinadi) flood.

Ilova (ssenariy):
    {"id": "tv", "type": "mcast", "src": "srv1", "group": "239.1.1.1",
     "dst": ["pc1", "pc3"], "rate": "4Mbps", "size": 1000, "start": 1, "stop": 29}
lab/apps.py uni har qabul qiluvchi uchun `udp` primitivlarga yoyadi (`mcast` = ilova id,
`group`). Dvigatellar bu primitivlarni guruhlaydi: bitta yuboruvchi, har kabelda BITTA nusxa.

Model: taqsimlash daraxti = manbadan har qabul qiluvchigacha eng qisqa unicast yo'llar
birlashmasi (SPT — PIM-SM'ning SPT'ga o'tgandan keyingi holati; RP orqali umumiy daraxt
modellanmaydi). Yo'ldagi har router'da pimd va kirish/chiqish interfeyslarida PIM bo'lishi
shart; aks holda bu qabul qiluvchiga oqim yetmaydi.
"""

import ipaddress

from lab.model import parse_ifname
from lab.validation_numbers import integer, packet_rate

MCAST_NET = ipaddress.ip_network("224.0.0.0/4")
LINK_LOCAL = ipaddress.ip_network("224.0.0.0/24")
MCAST_PORT = 5001


def receivers_of(a):
    if not isinstance(a, dict):
        raise ValueError("multicast ilova obyekt bo'lsin")
    r = a.get("dst") if a.get("dst") is not None else a.get("receivers")
    if isinstance(r, str):
        r = [x.strip() for x in r.split(",") if x.strip()]
    if r is None:
        return []
    if not isinstance(r, list) or any(not isinstance(name, str) or not name.strip() for name in r):
        raise ValueError("qabul qiluvchilar dst: [nom, ...] yoki vergul bilan ajratilgan nomlar bo'lsin")
    return list(r)


def validate_app(project, a):
    if not isinstance(a, dict):
        return ["multicast ilova obyekt bo'lsin"]
    errs = []
    try:
        group = a.get("group", "")
        if not isinstance(group, str):
            raise ValueError
        g = ipaddress.ip_address(group)
        if g not in MCAST_NET or g in LINK_LOCAL:
            raise ValueError
    except ValueError:
        errs.append("group — 224.0.1.0..239.255.255.255 multicast manzil (masalan 239.1.1.1)")
    try:
        rc = receivers_of(a)
    except ValueError as e:
        errs.append(str(e))
        rc = []
    devs = project.get("devices") if isinstance(project, dict) else None
    if not isinstance(devs, dict):
        devs = {}
    if not rc:
        errs.append("qabul qiluvchilar (dst: [...]) bo'sh")
    for r in rc:
        if r not in devs:
            errs.append(f"qabul qiluvchi '{r}' yo'q")
        elif not isinstance(devs[r], dict) or devs[r].get("type") not in ("host", "server"):
            errs.append(f"qabul qiluvchi '{r}' host/server bo'lsin")
    if a.get("src") in rc:
        errs.append("manba qabul qiluvchilar ichida bo'lmasin")
    try:
        from lab.model import parse_rate
        packet_rate(parse_rate(a.get("rate")), a.get("size", 1000))
    except ValueError as e:
        errs.append(str(e))
    try:
        # ns-3 multicast enables the 20-byte SeqTsSizeHeader in each payload.
        integer(a.get("size", 1000), "size", minimum=20, maximum=65507)
    except ValueError as e:
        errs.append(str(e))
    return errs


def validate_project(p):
    errs = []
    for name, d in (p.get("devices") or {}).items():
        pim = (d.get("config") or {}).get("pim")
        if not pim:
            continue
        if d.get("type") != "router":
            errs.append(f"{name}: PIM faqat router'da")
            continue
        if "pimd" not in ((d.get("config") or {}).get("daemons") or []):
            errs.append(f"{name}: config.pim bor, lekin daemons ichida pimd yo'q")
        if pim.get("rp"):
            try:
                ipaddress.ip_address(pim["rp"])
            except ValueError:
                errs.append(f"{name}: PIM rp noto'g'ri IP '{pim['rp']}'")
        for ifn in pim.get("interfaces") or []:
            if ifn not in ((d.get("config") or {}).get("interfaces") or {}):
                errs.append(f"{name}: PIM interfeysi '{ifn}' sozlanmagan")
    return errs


def pim_ifaces(dev):
    """None — hamma interfeyslar (pimd bo'lsa); set — ro'yxat; set() — PIM yo'q."""
    cfg = dev.get("config") or {}
    if dev.get("type") != "router" or "pimd" not in (cfg.get("daemons") or []):
        return set()
    lst = (cfg.get("pim") or {}).get("interfaces")
    return set(lst) if lst else None


def _pim_on(dev, ifn):
    s = pim_ifaces(dev)
    return s is None or ifn in s


def branch(net, src, dst_ip, hops):
    """Bitta qabul qiluvchiga shoxcha (Net.forward natijasi `hops` bo'yicha):
    -> (ok, sabab, [(qurilma, chiqish ifname)] — paketni L2 segmentga uzatgan nuqtalar)."""
    devs = net.devs
    seq = [src] + [h[0] for h in hops]
    tx = []
    for i, dv in enumerate(seq[:-1]):
        r = net.lookup(dv, dst_ip)
        if not r:
            return False, f"{dv}: marshrut yo'q", tx
        egress = r[2]
        tx.append((dv, egress))
        if i == 0:
            continue
        d = devs[dv]
        if d["type"] == "router":
            ingress = next((ifn for _p, ifn, ipi in net.ifaces(dv) if ipi and str(ipi.ip) == hops[i - 1][1]), None)
            if not pim_ifaces(d) and pim_ifaces(d) is not None:
                return False, f"{dv}: pimd yo'q — multicast uzatilmaydi", tx
            if not _pim_on(d, egress) or (ingress and not _pim_on(d, ingress)):
                return False, f"{dv}: {egress if not _pim_on(d, egress) else ingress} da PIM/IGMP o'chiq", tx
    return True, "ok", tx


def _snooping_domain(net, paths):
    """Segmentdagi barcha switchlar igmp_snooping yoqilganmi."""
    sws = {net.p["links"][lid][side]["dev"] for path in paths for lid, _d in path for side in ("a", "b")}
    sws = [s for s in sws if net.is_l2(s)]
    return bool(sws) and all((net.devs[s].get("config") or {}).get("igmp_snooping") for s in sws)


def flood_keys(net, tx_points, tree_keys):
    """Daraxtdan tashqari flood qilinadigan kabel yo'nalishlari: snooping o'chiq L2
    segmentlarda kadr butun domenga (barcha so'nggi nuqtalarga) yetadi."""
    extra = set()
    for dv, ifn in sorted(set(tx_points)):
        _d, port, vl = parse_ifname(ifn)
        if port is None:
            continue
        reach = net.l2_reach(dv, port, vl)
        paths = list(reach.values())
        if not paths or _snooping_domain(net, paths):
            continue
        for path in paths:
            for key in path:
                if key not in tree_keys:
                    extra.add(key)
    return extra


def demo_section(net, name):
    """Demo inspect: PIM interfeyslari va qo'shnilari (model)."""
    d = net.devs[name]
    rp = ((d.get("config") or {}).get("pim") or {}).get("rp") or "—"
    rows = []
    for port, ifn, ipi in net.ifaces(name):
        if not ipi or not _pim_on(d, ifn):
            continue
        nbr = [nd for (nd, _np, _tg) in net.l2_reach(name, port, parse_ifname(ifn)[2])
               if net.devs[nd]["type"] == "router" and "pimd" in ((net.devs[nd].get("config") or {}).get("daemons") or [])]
        rows.append([ifn, str(ipi.ip), ", ".join(sorted(set(nbr))) or "—", "ha", rp])
    return {"title": "PIM / IGMP (model)", "kind": "table",
            "columns": ["Interfeys", "IP", "PIM qo'shnilar", "IGMP", "RP"], "rows": rows}


def frr_config_lines(name, cfg, iface_names):
    """config.pim -> vtysh buyruqlari (FRR 8.x)."""
    pim = cfg.get("pim") or {}
    ifs = pim.get("interfaces") or iface_names
    lines = ["conf t"]
    if pim.get("rp"):
        lines.append(f"ip pim rp {pim['rp']} 224.0.0.0/4")
    for ifn in ifs:
        lines += [f"interface {ifn}", "ip pim", "ip igmp", "exit"]
    return lines
