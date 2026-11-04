"""Spanning Tree (802.1D STP / 802.1w RSTP) modeli — L2 halqalar.

Switch `config`:
    stp: true            — STP yoqilgan (OVS: stp_enable / rstp_enable)
    stp_mode: "stp" | "rstp"   (standart "stp")
    stp_priority: 0..61440, 4096 qadam (standart 32768)
    ports: {"<port>": {"stp_cost": N}}   — port yo'l narxi (standart — kabel tezligidan,
                                           802.1D-1998 jadvali; real driver ham shu qiymatni
                                           OVS'ga yozadi, chunki veth "10 Gbit/s" deb ko'rinadi)

compute(project) daraxtni hisoblaydi:
  * bridge id = (priority, qurilma id) — real driverda bridge MAC = device_mac(id, 0),
    shuning uchun tartib bir xil;
  * root — eng kichik bridge id; root port — (root narxi, qo'shni bridge id, qo'shni port id,
    o'z port id) bo'yicha eng yaxshi; har segmentda designated — (root narxi, bridge id, port id)
    bo'yicha yaxshirog'i; qolganlari alternate (blocking).
  * STP'siz bridge'lar (switch stp=false, P4 switch, Wi-Fi AP) BPDU'ni o'tkazmaydi (OVS
    forward-bpdu=false) — STP ular orqali halqani "ko'rmaydi". Blocking portlar olib
    tashlangandan keyin ham L2 grafda halqa qolsa — o'sha domen BROADCAST STORM.

Konvergensiya (analitik dvigatel, `hold` portlar): blocking -> forwarding o'tishi
802.1D da 2*forward_delay (30 s; nosozlik bilvosita — +max_age 20 s), RSTP da ~1 s.
"""

L2_TYPES = ("switch", "p4switch", "wifiap")
DEFAULT_PRIORITY = 32768
FORWARD_DELAY = 15
MAX_AGE = 20
RSTP_CONV = 1.0
STP_MODES = ("stp", "rstp")

# 802.1D-1998 (OVS STP ham shu "qisqa" narxlar)
_COST_TABLE = [(10000, 2), (1000, 4), (622, 6), (155, 14), (100, 19), (45, 39), (16, 62), (10, 100), (4, 250)]


def default_cost(bw_mbps):
    for bw, c in _COST_TABLE:
        if float(bw_mbps) >= bw:
            return c
    return 2000


def stp_enabled(d):
    cfg = d.get("config") or {}
    return d.get("type") == "switch" and bool(cfg.get("stp")) and cfg.get("mode") != "sdn"


def stp_mode(d):
    return ((d.get("config") or {}).get("stp_mode") or "stp").lower()


def bridge_priority(d):
    try:
        return int((d.get("config") or {}).get("stp_priority", DEFAULT_PRIORITY))
    except (TypeError, ValueError):
        return DEFAULT_PRIORITY


def port_cost(project, dev, port, lid):
    pc = (((project["devices"][dev].get("config") or {}).get("ports") or {}).get(str(port)) or {})
    if pc.get("stp_cost") not in (None, ""):
        return int(pc["stp_cost"])
    return default_cost(((project["links"][lid].get("params") or {}).get("bw", 100)))


def validate(project):
    errs = []
    for name, d in (project.get("devices") or {}).items():
        cfg = d.get("config") or {}
        if "stp_priority" in cfg and cfg["stp_priority"] not in (None, ""):
            try:
                p = int(cfg["stp_priority"])
                if not 0 <= p <= 61440 or p % 4096:
                    raise ValueError
            except (TypeError, ValueError):
                errs.append(f"{name}: stp_priority 0..61440, 4096 ga karrali (masalan 4096, 32768)")
        if cfg.get("stp_mode") not in (None, "") and str(cfg["stp_mode"]).lower() not in STP_MODES:
            errs.append(f"{name}: stp_mode stp | rstp")
        for pk, pc in (cfg.get("ports") or {}).items():
            if (pc or {}).get("stp_cost") not in (None, ""):
                try:
                    if not 1 <= int(pc["stp_cost"]) <= 200000000:
                        raise ValueError
                except (TypeError, ValueError):
                    errs.append(f"{name}: port {pk} stp_cost 1..200000000")
    return errs


def _link_up(project, lid):
    l = project["links"].get(lid)
    if not l or l.get("up") is False:
        return False
    if (l.get("params") or {}).get("medium") == "wifi":
        from lab.wifi import link_estimate
        est = link_estimate(project, lid)
        return bool(est and est["ok"])
    return True


def compute(project, hold=()):
    """-> {"bridges": {sw: {...}}, "blocked": set((sw, port)), "storm": set(bridge),
            "storm_cycle": {bridge: [link ids]}}.
    hold — (sw, port) juftlari: konvergensiya tugaguncha blocking'da ushlanadigan portlar."""
    devs = project["devices"]
    hold = {(a, int(b)) for a, b in hold}
    bridges = {n for n, d in devs.items() if d.get("type") in L2_TYPES}
    stp_br = {n for n in bridges if stp_enabled(devs[n])}
    bid = {n: (bridge_priority(devs[n]), int(devs[n].get("id", 0))) for n in stp_br}
    # STP segmentlari: ikkala uchi ham STP switch bo'lgan ishlaydigan kabellar
    seg = []           # (lid, a, pa, b, pb)
    for lid, l in project["links"].items():
        a, b = l["a"]["dev"], l["b"]["dev"]
        if a in stp_br and b in stp_br and _link_up(project, lid):
            seg.append((lid, a, l["a"]["port"], b, l["b"]["port"]))
    # komponentlar bo'yicha root + root narxi (Dijkstra, kichik daraxtlar — oddiy relaks)
    adj = {n: [] for n in stp_br}
    for lid, a, pa, b, pb in seg:
        adj[a].append((pa, b, pb, port_cost(project, a, pa, lid), lid))
        adj[b].append((pb, a, pa, port_cost(project, b, pb, lid), lid))
    comp = {}
    for n in sorted(stp_br, key=lambda x: bid[x]):
        if n in comp:
            continue
        stack, members = [n], []
        comp[n] = n
        while stack:
            x = stack.pop()
            members.append(x)
            for _pa, y, _pb, _c, _l in adj[x]:
                if y not in comp:
                    comp[y] = n
                    stack.append(y)
    root_of = {}
    for n in stp_br:
        root_of[n] = min((m for m in stp_br if comp[m] == comp[n]), key=lambda m: bid[m])
    INF = float("inf")
    rc = {n: (0 if root_of[n] == n else INF) for n in stp_br}
    best = {n: None for n in stp_br}      # (rc, nbr bid, nbr port, own port, own port no)
    for _ in range(len(stp_br) + 1):
        changed = False
        for x in stp_br:
            if root_of[x] == x:
                continue
            cand = None
            for pa, y, pb, c, _l in adj[x]:
                if rc[y] == INF:
                    continue
                key = (rc[y] + c, bid[y], (128, pb), (128, pa))
                if cand is None or key < cand[0]:
                    cand = (key, pa)
            if cand and (best[x] is None or cand[0] != best[x][0]):
                best[x] = cand
                rc[x] = cand[0][0]
                changed = True
        if not changed:
            break
    root_port = {x: (best[x][1] if best[x] else None) for x in stp_br}
    roles = {x: {} for x in stp_br}
    for lid, a, pa, b, pb in seg:
        ka = (rc[a], bid[a], (128, pa))
        kb = (rc[b], bid[b], (128, pb))
        des, oth = ((a, pa), (b, pb)) if ka <= kb else ((b, pb), (a, pa))
        roles[des[0]][des[1]] = "designated"
        roles[oth[0]][oth[1]] = "root" if root_port[oth[0]] == oth[1] else "alternate"
    blocked = set()
    for x in stp_br:
        for p, (lid, _peer, _pp) in _ports(project, x).items():
            if not _link_up(project, lid):
                roles[x][p] = "disabled"
            elif p not in roles[x]:
                roles[x][p] = "designated"           # chekka port (host/router/STP'siz bridge)
            if roles[x][p] == "alternate" or (x, p) in hold:
                blocked.add((x, p))
    info = {}
    for x in stp_br:
        r = root_of[x]
        ports = {}
        for p in sorted(roles[x]):
            role = roles[x][p]
            state = ("disabled" if role == "disabled" else
                     "blocking" if (x, p) in blocked else "forwarding")
            if (x, p) in hold and role != "alternate":
                state = "listening"            # konvergensiya: hali forwarding emas
            ports[p] = {"role": role, "state": state}
        info[x] = {"bridge_id": _bid_str(bid[x], devs[x]), "root_id": _bid_str(bid[r], devs[r]), "root": r,
                   "is_root": r == x, "root_port": root_port[x], "root_cost": 0 if rc[x] == INF else rc[x],
                   "mode": stp_mode(devs[x]), "ports": ports}
    storm, cyc = _storms(project, bridges, blocked)
    return {"bridges": info, "blocked": blocked, "storm": storm, "storm_cycle": cyc}


def _bid_str(b, d):
    from lab.model import device_mac
    return f"{b[0]}.{device_mac(int(d.get('id', 0)), 0)}"


def _ports(project, dev):
    out = {}
    for lid, l in project["links"].items():
        if l["a"]["dev"] == dev:
            out[l["a"]["port"]] = (lid, l["b"]["dev"], l["b"]["port"])
        if l["b"]["dev"] == dev:
            out[l["b"]["port"]] = (lid, l["a"]["dev"], l["a"]["port"])
    return out


def _storms(project, bridges, blocked):
    """Blocking portlarsiz L2 forwarding graf (faqat bridge-bridge kabellar) — halqa bormi.
    Multigraf: komponentda kabellar soni >= bridge'lar soni bo'lsa halqa (storm)."""
    edges = []
    for lid, l in project["links"].items():
        a, b = l["a"]["dev"], l["b"]["dev"]
        if a in bridges and b in bridges and _link_up(project, lid) \
                and (a, l["a"]["port"]) not in blocked and (b, l["b"]["port"]) not in blocked:
            edges.append((lid, a, b))
    parent = {n: n for n in bridges}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for _lid, a, b in edges:
        parent[root(a)] = root(b)
    cnt_n, cnt_e, lids = {}, {}, {}
    for n in bridges:
        cnt_n[root(n)] = cnt_n.get(root(n), 0) + 1
    for lid, a, _b in edges:
        r = root(a)
        cnt_e[r] = cnt_e.get(r, 0) + 1
        lids.setdefault(r, []).append(lid)
    storm, cyc = set(), {}
    for r, ne in cnt_e.items():
        if ne >= cnt_n[r]:
            members = {n for n in bridges if root(n) == r}
            storm |= members
            for n in members:
                cyc[n] = sorted(lids[r])
    return storm, cyc


def forwarding_ports(st):
    """{sw: {port: state}} — o'zgarishlarni kuzatish uchun."""
    return {x: {p: v["state"] for p, v in b["ports"].items()} for x, b in st["bridges"].items()}


def convergence_time(project, sw, failed_links, sc=None):
    """Port blocking -> forwarding o'tish vaqti (s). sc.stp_convergence_s — qo'lda."""
    if sc and sc.get("stp_convergence_s") not in (None, ""):
        return float(sc["stp_convergence_s"])
    if stp_mode(project["devices"][sw]) == "rstp":
        return RSTP_CONV
    if not failed_links:          # kabel ulandi / yangi port: listening + learning
        return 2 * FORWARD_DELAY
    direct = any(project["links"][lid]["a"]["dev"] == sw or project["links"][lid]["b"]["dev"] == sw
                 for lid in failed_links if lid in project["links"])
    # bilvosita nosozlik: eski root ma'lumoti max_age davomida eskirishini kutadi
    return 2 * FORWARD_DELAY + (0 if direct else MAX_AGE)


def inspect_section(project, sw, st=None):
    """Inspect jadvali (model): bridge/root id, root port, port rollari va holatlari."""
    st = st or compute(project, project.get("_stp_hold") or ())
    b = st["bridges"].get(sw)
    if not b:
        return None
    rows = [[p, v["role"], v["state"]] for p, v in sorted(b["ports"].items())]
    hint = (f"rejim {b['mode'].upper()}, bridge {b['bridge_id']}, root {b['root_id']} ({b['root']})"
            + (" — BU ROOT" if b["is_root"] else f", root port {b['root_port']}, root narxi {b['root_cost']}"))
    if sw in st["storm"]:
        hint += " — DIQQAT: L2 halqa, broadcast storm"
    return {"title": "STP (spanning tree)", "kind": "table", "columns": ["Port", "Rol", "Holat"],
            "rows": rows, "hint": hint}
