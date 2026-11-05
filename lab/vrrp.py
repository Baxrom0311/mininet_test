"""VRRP (RFC 5798, VRRPv3) — birinchi-hop gateway zaxirasi.

Router `config.vrrp`:
    [{"iface": "r1-eth1", "vrid": 1, "vip": "10.0.1.254/24", "priority": 120,
      "preempt": true, "advert_ms": 1000}]
Bir xil (vrid, VIP) ga ega routerlar — bitta guruh. Hostlar gateway sifatida VIP'ni oladi.

Saylov: interfeysi (kabeli) ishlayotgan routerlar ichida eng yuqori `priority`
(teng bo'lsa — interfeysning asosiy IP'si kattasi). Master VIP egasi bo'ladi.

Vaqt modeli (analitik va ns-3 uchun umumiy — timeline()):
  * master kabeli uziladi -> VIP egasiz; backup `Master_Down_Interval` dan keyin master:
        MDI = 3 * advert + skew,  skew = (256 - priority) / 256 * advert
    (standart advert 1 s, priority 100 -> ~3.6 s). Scenario `vrrp_convergence_s` — qo'lda.
  * yuqori prioritetli router qaytadi (preempt=true) -> darhol master bo'ladi (FRR 8.4 vrrpd
    shunday ishlaydi; `vrrp_preempt_s` bilan kechiktirish mumkin); trafik uzilmaydi.
  * Eslatma: javob yo'lidagi IGP (masalan OSPF LAN'da DR/dead-interval) konvergensiyasi
    alohida — analitik modelda u darhol deb olinadi.
"""

import ipaddress

from lab.model import parse_ifname


def entries(project):
    """[(group_key, router, entry)] — group_key = "vrid/vip_ip"."""
    out = []
    for name, d in sorted(project["devices"].items(), key=lambda kv: kv[1].get("id", 0)):
        if d.get("type") != "router":
            continue
        for e in (d.get("config") or {}).get("vrrp") or []:
            try:
                vip = ipaddress.ip_interface(e["vip"])
            except (KeyError, ValueError):
                continue
            out.append((f"{int(e.get('vrid', 1))}/{vip.ip}", name, e))
    return out


def groups(project):
    g = {}
    for key, r, e in entries(project):
        g.setdefault(key, []).append((r, e))
    return g


def has_vrrp(project):
    return bool(entries(project))


def _prio(e):
    return int(e.get("priority", 100))


def _primary_ip(project, router, ifn):
    ip = (((project["devices"][router].get("config") or {}).get("interfaces") or {}).get(ifn) or {}).get("ip")
    try:
        return int(ipaddress.ip_interface(ip).ip)
    except (TypeError, ValueError):
        return 0


def eligible(project, router, e):
    """Router VRRP interfeysining kabeli ishlayaptimi."""
    _d, port, _v = parse_ifname(e.get("iface", ""))
    for l in project["links"].values():
        for side in ("a", "b"):
            if l[side]["dev"] == router and l[side]["port"] == port:
                return l.get("up") is not False
    return False


def elect(project, members):
    cands = [(_prio(e), _primary_ip(project, r, e["iface"]), r) for r, e in members if eligible(project, r, e)]
    return max(cands)[2] if cands else None


def masters(project):
    """{group_key: master|None}. project["_vrrp_master"] (analitik/ns-3 vaqt modeli) ustun."""
    ov = project.get("_vrrp_master")
    out = {}
    for key, members in groups(project).items():
        out[key] = ov[key] if ov is not None and key in ov else elect(project, members)
    return out


def vip_ifaces(project, dev):
    """Master bo'lgan router uchun qo'shimcha interfeys manzillari: [(port, ifname, IPv4Interface)]."""
    m = masters(project)
    out = []
    for key, r, e in entries(project):
        if r == dev and m.get(key) == dev:
            _d, port, _v = parse_ifname(e["iface"])
            out.append((port, e["iface"], ipaddress.ip_interface(e["vip"])))
    return out


def mdi(e, sc=None):
    """Master_Down_Interval (s) — backup master bo'lguncha."""
    if sc and sc.get("vrrp_convergence_s") not in (None, ""):
        return float(sc["vrrp_convergence_s"])
    adv = float(e.get("advert_ms", 1000)) / 1000
    return 3 * adv + (256 - _prio(e)) / 256 * adv


def preempt_delay(e, sc=None):
    """Yuqori prioritetli router qaytganda master bo'lguncha (s). RFC 5798 bo'yicha MDI kutish
    mumkin, lekin FRR 8.4 vrrpd preempt'ni darhol bajaradi (real o'lchov: ~0.03 s) — shunga
    moslangan. Scenario `vrrp_preempt_s` — qo'lda. Bu vaqtda eski master forward qiladi."""
    if sc and sc.get("vrrp_preempt_s") not in (None, ""):
        return float(sc["vrrp_preempt_s"])
    return 0.0


def timeline(project, sc):
    """Topologiya hodisalari bo'yicha master o'zgarishlari: [(t, group_key, master|None)].
    t=0 dagi boshlang'ich saylov ham kiradi (konvergensiyasiz)."""
    import copy
    if not has_vrrp(project):
        return []
    p = copy.deepcopy(project)
    dur = float(sc.get("duration", 30))
    evs = sorted((e for e in sc.get("events") or [] if e.get("action") in ("link_down", "link_up")),
                 key=lambda e: float(e.get("t", 0)))
    out = []
    state = {}
    for key, members in groups(p).items():
        m = elect(p, members)
        state[key] = {"cur": m, "pending": None}
        out.append((0.0, key, m))

    def entry_of(key, r):
        return next(e for rr, e in groups(p)[key] if rr == r)

    def commit_until(t):
        for key, st in state.items():
            pd = st["pending"]
            if pd and pd[0] <= t + 1e-9:
                st["cur"], st["pending"] = pd[1], None
                out.append((round(pd[0], 3), key, pd[1]))

    for ev in evs:
        t = float(ev.get("t", 0))
        commit_until(t)
        l = p["links"].get(ev.get("link"))
        if not l:
            continue
        l["up"] = ev["action"] == "link_up"
        for key, members in groups(p).items():
            st = state[key]
            cur = st["cur"]
            if cur is not None and not eligible(p, cur, entry_of(key, cur)):
                st["cur"] = cur = None
                out.append((t, key, None))
            d = elect(p, members)
            if d is None:
                st["pending"] = None
            elif cur is None:
                if not (st["pending"] and st["pending"][1] == d):
                    st["pending"] = (t + mdi(entry_of(key, d), sc), d)
            elif d != cur and _prio(entry_of(key, d)) > _prio(entry_of(key, cur)) \
                    and entry_of(key, d).get("preempt", True) is not False:
                if not (st["pending"] and st["pending"][1] == d):
                    st["pending"] = (t + preempt_delay(entry_of(key, d), sc), d)
            else:
                st["pending"] = None
    commit_until(dur)
    return out


def masters_at(tl, t):
    """timeline'dan t vaqtdagi {group_key: master}."""
    m = {}
    for tt, key, r in tl:
        if tt <= t + 1e-9:
            m[key] = r
    return m


def validate(project):
    errors = []
    devs = project.get("devices") or {}
    for name, d in devs.items():
        vr = (d.get("config") or {}).get("vrrp")
        if not vr:
            continue
        if d.get("type") != "router":
            errors.append(f"{name}: VRRP faqat router'da")
            continue
        if not isinstance(vr, list):
            errors.append(f"{name}: vrrp ro'yxat bo'lsin")
            continue
        ics = (d.get("config") or {}).get("interfaces") or {}
        seen = set()
        for e in vr:
            w = f"{name}: VRRP {e.get('vrid')}"
            try:
                vrid = int(e.get("vrid"))
                if not 1 <= vrid <= 255:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{w}: vrid 1..255")
                continue
            if vrid in seen:
                errors.append(f"{w}: vrid takrorlangan")
            seen.add(vrid)
            try:
                if not 1 <= int(e.get("priority", 100)) <= 254:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{w}: priority 1..254")
            try:
                if not 10 <= int(e.get("advert_ms", 1000)) <= 40950:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{w}: advert_ms 10..40950")
            ifn = e.get("iface")
            ip = (ics.get(ifn) or {}).get("ip")
            try:
                ipi = ipaddress.ip_interface(ip)
                vip = ipaddress.ip_interface(e.get("vip"))
                if vip.ip not in ipi.network:
                    errors.append(f"{w}: VIP {vip.ip} {ifn} subnetida ({ipi.network}) emas")
                elif vip.ip == ipi.ip:
                    errors.append(f"{w}: VIP interfeysning o'z IP'si bilan bir xil (owner rejimi qo'llab-quvvatlanmaydi)")
            except (TypeError, ValueError):
                errors.append(f"{w}: interfeys '{ifn}' statik IP bilan bo'lsin va vip to'g'ri bo'lsin (10.0.1.254/24)")
    # guruh a'zolari VIP'ni bir xil prefiks bilan yozsin
    for key, members in groups(project).items():
        pref = {ipaddress.ip_interface(e["vip"]).network.prefixlen for _r, e in members}
        if len(pref) > 1:
            errors.append(f"VRRP {key}: guruh a'zolarida VIP prefiksi har xil")
    return errors


def frr_config(dev):
    """FRR vtysh konfiguratsiyasi (VRRPv3)."""
    lines = []
    for e in (dev.get("config") or {}).get("vrrp") or []:
        v = int(e["vrid"])
        lines += [f"interface {e['iface']}", f" vrrp {v} version 3", f" vrrp {v} priority {_prio(e)}",
                  f" vrrp {v} advertisement-interval {int(e.get('advert_ms', 1000))}",
                  f" vrrp {v} ip {ipaddress.ip_interface(e['vip']).ip}"]
        lines.append(f" {'no ' if e.get('preempt', True) is False else ''}vrrp {v} preempt")
        lines.append("exit")
    return "\n".join(lines) + "\n"


def macvlan_name(e):
    """Router namespace ichidagi macvlan nomi (FRR vrrpd uni VRRP MAC bo'yicha topadi)."""
    _d, port, _v = parse_ifname(e["iface"])
    return f"vrrp4-{port}-{int(e['vrid'])}"


def vmac(vrid):
    return f"00:00:5e:00:01:{int(vrid):02x}"


def model_rows(project, router):
    """Inspect jadvali: [VRID, interfeys, VIP, prioritet, holat, master]."""
    m = masters(project)
    rows = []
    for key, r, e in entries(project):
        if r != router:
            continue
        mm = m.get(key)
        state = "Master" if mm == router else ("Initialize" if not eligible(project, r, e) else "Backup")
        rows.append([int(e["vrid"]), e["iface"], e["vip"], _prio(e), state, mm or "—"])
    return rows
