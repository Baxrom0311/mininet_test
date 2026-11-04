"""Tarmoq xizmatlari modeli: DHCP (manzil berish) va NAT (masquerade).

DHCP
----
Server (router/server) — `config.dhcp_server`:
    {"pools": [{"iface": "r1-eth1", "start": "10.0.1.100", "end": "10.0.1.200",
                "gateway": "10.0.1.1", "lease": 3600}]}
    gateway bo'lmasa — server interfeysi IP'si.
Mijoz — interfeys IP'si "dhcp": `{"interfaces": {"pc1-eth1": {"ip": "dhcp"}}}`.

resolve_dhcp() manzillarni DETERMINISTIK taqsimlaydi: mijozlar (qurilma id, interfeys)
tartibida, pool'dagi statik IP'lar va oldingi lizinglardan bo'sh birinchi manzil.
Barcha dvigatellar shu natijani ishlatadi; real driverda dnsmasq shu manzillarni
MAC bo'yicha rezervatsiya (dhcp-host) sifatida beradi — model == haqiqat.
Mijoz server bilan bitta L2 domenda (VLAN hisobga olinadi) bo'lishi kerak (relay yo'q).

NAT
---
Router `config.nat = {"outside": "r1-eth2"}` — boshqa interfeyslardan kelib outside
interfeysdan chiqadigan paketlar manbasi outside IP bilan almashtiriladi (iptables
MASQUERADE). Javob yo'li: tashqi tomon routerning outside IP'siga qaytaradi, router
ichki manzilga uzatadi (demo_driver.Net.reply).
"""

import copy
import ipaddress

from lab.model import device_mac, parse_ifname


def dhcp_pools(project):
    """[(server, iface, IPv4Interface, pool dict)] — server interfeysi statik IP'li bo'lishi shart."""
    out = []
    for name, d in sorted(project["devices"].items(), key=lambda kv: kv[1].get("id", 0)):
        cfg = d.get("config") or {}
        for pool in (cfg.get("dhcp_server") or {}).get("pools") or []:
            ip = ((cfg.get("interfaces") or {}).get(pool.get("iface")) or {}).get("ip")
            try:
                ipi = ipaddress.ip_interface(ip)
            except (ValueError, TypeError):
                continue
            out.append((name, pool["iface"], ipi, pool))
    return out


def validate_services(project):
    errors = []
    devs = project.get("devices") or {}
    for name, d in devs.items():
        cfg = d.get("config") or {}
        srv = cfg.get("dhcp_server")
        if srv:
            if d.get("type") not in ("router", "server"):
                errors.append(f"{name}: DHCP server faqat router/server'da")
            for pool in srv.get("pools") or []:
                ifn = pool.get("iface")
                ip = ((cfg.get("interfaces") or {}).get(ifn) or {}).get("ip")
                try:
                    ipi = ipaddress.ip_interface(ip)
                    a, b = ipaddress.ip_address(pool.get("start")), ipaddress.ip_address(pool.get("end"))
                    if a not in ipi.network or b not in ipi.network or a > b:
                        raise ValueError
                    if pool.get("gateway"):
                        ipaddress.ip_address(pool["gateway"])
                except (ValueError, TypeError):
                    errors.append(f"{name}: DHCP pool {ifn}: interfeysda statik IP bo'lsin, start..end shu subnetda")
        nat = cfg.get("nat")
        if nat:
            out = nat.get("outside")
            if d.get("type") != "router":
                errors.append(f"{name}: NAT faqat router'da")
            elif out not in (cfg.get("interfaces") or {}) or not ((cfg["interfaces"][out] or {}).get("ip")):
                errors.append(f"{name}: NAT outside interfeysi '{out}' IP bilan sozlanmagan")
    from lab.firewall import validate_firewall
    errors += validate_firewall(project)
    return errors


def has_dhcp_clients(project):
    return any((ic or {}).get("ip", "").lower() == "dhcp"
               for d in project["devices"].values()
               for ic in ((d.get("config") or {}).get("interfaces") or {}).values())


def resolve_dhcp(project):
    """-> (loyiha nusxasi "dhcp" o'rniga manzillar bilan, lizinglar ro'yxati).
    lizing: {client, iface, ip, mac, server, server_iface, gateway, lease}."""
    from lab.drivers.demo_driver import Net
    if not has_dhcp_clients(project):
        return project, []
    p = copy.deepcopy(project)
    net = Net(p, resolve=False)
    pools = dhcp_pools(p)
    used = set()
    for d in p["devices"].values():
        for ic in ((d.get("config") or {}).get("interfaces") or {}).values():
            try:
                used.add(ipaddress.ip_interface(ic.get("ip")).ip)
            except (ValueError, TypeError):
                pass
    for pool in pools:
        try:
            used.add(ipaddress.ip_address(pool[3].get("gateway")))
        except (ValueError, TypeError):
            pass
    leases = []
    clients = []
    for name, d in sorted(p["devices"].items(), key=lambda kv: kv[1].get("id", 0)):
        for ifn, ic in sorted(((d.get("config") or {}).get("interfaces") or {}).items()):
            if (ic or {}).get("ip", "").lower() == "dhcp":
                clients.append((name, ifn))
    for name, ifn in clients:
        _dn, port, vlan = parse_ifname(ifn)
        if port is None:
            continue
        reach = net.l2_reach(name, port, vlan)
        chosen = None
        for srv, sifn, sip, pool in pools:
            _sd, sport, svlan = parse_ifname(sifn)
            if (srv, sport, svlan) in reach:
                chosen = (srv, sifn, sip, pool)
                break
        ic = p["devices"][name]["config"]["interfaces"][ifn]
        if not chosen:
            ic["ip"] = ""          # server topilmadi — manzil yo'q (APIPA'siz)
            continue
        srv, sifn, sip, pool = chosen
        a, b = int(ipaddress.ip_address(pool["start"])), int(ipaddress.ip_address(pool["end"]))
        addr = next((ipaddress.ip_address(x) for x in range(a, b + 1) if ipaddress.ip_address(x) not in used), None)
        if addr is None:
            ic["ip"] = ""          # pool tugadi
            continue
        used.add(addr)
        ic["ip"] = f"{addr}/{sip.network.prefixlen}"
        gw = pool.get("gateway") or str(sip.ip)
        cfg = p["devices"][name]["config"]
        if p["devices"][name]["type"] in ("host", "server") and not cfg.get("gateway"):
            cfg["gateway"] = gw
        leases.append({"client": name, "iface": ifn, "ip": str(addr), "prefix": sip.network.prefixlen,
                       "mac": device_mac(p["devices"][name]["id"], port), "server": srv, "server_iface": sifn,
                       "gateway": gw, "lease": int(pool.get("lease", 3600))})
    return p, leases
