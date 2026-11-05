"""Link aggregation (802.3ad LACP / statik bond) — switch'lar orasida.

Switch `config.bonds`:
    {"bond1": {"ports": [3, 4], "mode": "balance-tcp" | "balance-slb" | "active-backup",
               "lacp": "active" | "passive" | "off"}}
Qo'shni switch'da xuddi shu kabellarning ikkinchi uchlari bilan mos bond bo'lishi shart.
Host/router bond'lari qo'llab-quvvatlanmaydi (Mininet konteyner kernelida bonding yo'q;
OVS bond faqat switch'lar orasida).

Model:
  * bond — bitta mantiqiy L2 qo'shnichilik: STP uni BITTA port deb ko'radi (a'zolar halqa emas);
  * har oqim bitta a'zo kabel orqali o'tadi (deterministik hash):
      balance-tcp   — oqim kaliti (manba, manzil, ilova) bo'yicha (L4 hash),
      balance-slb   — manba qurilma (MAC) bo'yicha,
      active-backup — eng kichik portli ishlaydigan a'zo;
  * a'zo uzilsa (carrier yo'qoldi) — `bond_failover_s` (standart 0.5 s) o'tguncha shu a'zoga
    hash qilingan oqimlar yo'qotiladi, keyin qolgan a'zolarga qayta taqsimlanadi.
"""

import zlib

BOND_MODES = ("balance-tcp", "balance-slb", "active-backup")
LACP_MODES = ("active", "passive", "off")
DEFAULT_FAILOVER = 0.5


def bonds_of(d):
    if d.get("type") != "switch":
        return {}
    return (d.get("config") or {}).get("bonds") or {}


def _peer(project, dev, port):
    for lid, l in project["links"].items():
        if l["a"]["dev"] == dev and l["a"]["port"] == port:
            return lid, l["b"]["dev"], l["b"]["port"]
        if l["b"]["dev"] == dev and l["b"]["port"] == port:
            return lid, l["a"]["dev"], l["a"]["port"]
    return None


def groups(project):
    """Mos bond juftlari: [{"a": (sw, bond), "b": (sw2, bond2), "links": [lid..] (port tartibida),
    "ports_a": {lid: port}, "ports_b": {lid: port}, "mode", "lacp": (la, lb)}]. Xato juftlar tashlanadi."""
    out, seen = [], set()
    devs = project.get("devices") or {}
    for sw, d in sorted(devs.items()):
        for bn, b in sorted(bonds_of(d).items()):
            if (sw, bn) in seen:
                continue
            ports = [int(p) for p in b.get("ports") or []]
            peers = [_peer(project, sw, p) for p in ports]
            if len(ports) < 2 or any(x is None for x in peers):
                continue
            psw = {x[1] for x in peers}
            if len(psw) != 1:
                continue
            psw = psw.pop()
            pports = {x[2] for x in peers}
            pb = next((n2 for n2, b2 in bonds_of(devs.get(psw) or {}).items()
                       if {int(p) for p in b2.get("ports") or []} == pports), None)
            if pb is None:
                continue
            seen |= {(sw, bn), (psw, pb)}
            b2 = bonds_of(devs[psw])[pb]
            order = sorted(zip(ports, peers))
            out.append({"a": (sw, bn), "b": (psw, pb), "links": [x[1][0] for x in order],
                        "ports_a": {x[1][0]: x[0] for x in order}, "ports_b": {x[1][0]: x[1][2] for x in order},
                        "mode": b.get("mode") or "balance-tcp",
                        "lacp": (b.get("lacp") or "active", b2.get("lacp") or "active")})
    return out


def member_ports(project):
    """{(sw, port): group} — bond a'zosi bo'lgan switch portlari."""
    out = {}
    for g in groups(project):
        for lid in g["links"]:
            out[(g["a"][0], g["ports_a"][lid])] = g
            out[(g["b"][0], g["ports_b"][lid])] = g
    return out


def validate(project):
    errs = []
    devs = project.get("devices") or {}
    used = {}
    for sw, d in devs.items():
        bonds = (d.get("config") or {}).get("bonds") or {}
        if bonds and d.get("type") != "switch":
            errs.append(f"{sw}: bond (link aggregation) faqat switch'da (host/router bonding qo'llab-quvvatlanmaydi)")
            continue
        for bn, b in bonds.items():
            w = f"{sw}/{bn}"
            if not str(bn) or len(str(bn)) > 15:
                errs.append(f"{w}: bond nomi 1..15 belgi")
            if (b.get("mode") or "balance-tcp") not in BOND_MODES:
                errs.append(f"{w}: mode {' | '.join(BOND_MODES)}")
            if (b.get("lacp") or "active") not in LACP_MODES:
                errs.append(f"{w}: lacp {' | '.join(LACP_MODES)}")
            try:
                ports = [int(p) for p in b.get("ports") or []]
            except (TypeError, ValueError):
                errs.append(f"{w}: ports — port raqamlari ro'yxati")
                continue
            if len(ports) < 2:
                errs.append(f"{w}: kamida 2 a'zo port kerak")
                continue
            peers = []
            for p in ports:
                if (sw, p) in used:
                    errs.append(f"{w}: port {p} '{used[(sw, p)]}' bond'ida ham bor")
                used[(sw, p)] = bn
                pr = _peer(project, sw, p)
                if pr is None:
                    errs.append(f"{w}: port {p} ga kabel ulanmagan")
                else:
                    peers.append(pr)
            if len(peers) != len(ports):
                continue
            psw = {x[1] for x in peers}
            if len(psw) != 1:
                errs.append(f"{w}: barcha a'zo kabellar bitta qo'shni switch'ga borishi kerak ({', '.join(sorted(psw))})")
                continue
            psw = psw.pop()
            if (devs.get(psw) or {}).get("type") != "switch":
                errs.append(f"{w}: qo'shni '{psw}' switch emas — bond faqat switch <-> switch")
                continue
            pports = {x[2] for x in peers}
            match = [(n2, b2) for n2, b2 in bonds_of(devs[psw]).items()
                     if {int(p) for p in b2.get("ports") or []} == pports]
            if not match:
                errs.append(f"{w}: {psw} da shu kabellar ({', '.join(map(str, sorted(pports)))}-portlar) uchun mos bond yo'q")
                continue
            la, lb = (b.get("lacp") or "active"), (match[0][1].get("lacp") or "active")
            if la == "passive" and lb == "passive":
                errs.append(f"{w}: ikkala tomon ham lacp=passive — LACP muzokarasi boshlanmaydi")
            elif (la == "off") != (lb == "off"):
                errs.append(f"{w}: lacp mos emas ({la} vs {psw}/{match[0][0]}: {lb}) — ikkalasi off yoki ikkalasi LACP")
    return errs


def failover_s(sc=None):
    if sc and sc.get("bond_failover_s") not in (None, ""):
        return float(sc["bond_failover_s"])
    return DEFAULT_FAILOVER


def _hash(key):
    return zlib.crc32(repr(key).encode())


def choose(group, usable, sender, flow_key):
    """Oqim uchun a'zo kabel (lid) yoki None. usable — tanlanishi mumkin bo'lgan a'zolar
    (ishlaydigan + failover tugamagan uzilganlar), port tartibida."""
    if not usable:
        return None
    mode = group["mode"]
    if mode == "active-backup":
        return usable[0]
    key = sender if mode == "balance-slb" else (flow_key if flow_key is not None else sender)
    return usable[_hash(key) % len(usable)]


def usable_members(project, group, link_ok):
    """Hash uchun a'zolar: ishlaydiganlar + `_lag_hold` (uzilgan, lekin failover hali tugamagan)."""
    hold = set(project.get("_lag_hold") or ())
    return [lid for lid in group["links"] if link_ok(lid) or lid in hold]


def inspect_section(project, sw, link_ok):
    rows = []
    for g in groups(project):
        for side, other in (("a", "b"), ("b", "a")):
            if g[side][0] != sw:
                continue
            usable = usable_members(project, g, link_ok)
            ports = g["ports_" + side]
            for lid in g["links"]:
                rows.append([g[side][1], ports[lid], lid, f"{g[other][0]}:{g['ports_' + other][lid]}",
                             "enabled" if link_ok(lid) else "disabled",
                             "active" if (g["mode"] != "active-backup" or (usable and usable[0] == lid)) and link_ok(lid)
                             else "standby" if link_ok(lid) else "—"])
    if not rows:
        return None
    gs = [g for g in groups(project) if sw in (g["a"][0], g["b"][0])]
    hint = "; ".join(f"{g['a'][1] if g['a'][0] == sw else g['b'][1]}: {g['mode']}, lacp "
                     f"{g['lacp'][0] if g['a'][0] == sw else g['lacp'][1]}, "
                     f"{sum(1 for lid in g['links'] if link_ok(lid))}/{len(g['links'])} a'zo" for g in gs)
    return {"title": "Link aggregation (bond)", "kind": "table",
            "columns": ["Bond", "Port", "Kabel", "Qo'shni", "Holat", "Rol"], "rows": rows, "hint": hint}
