#!/usr/bin/env python3
"""NetLab -> ns-3 (diskret-hodisali simulyatsiya) runner.

Alohida jarayon sifatida ishga tushiriladi (ns-3 Simulator global singleton,
import ~10s, C++ xatolari serverni yiqitmasin):

    python3 -m lab.engines.ns3_runner input.json output.json

input.json:  {"project": {...}, "scenario": {...}}
stdout:      har qatorda JSON: {"progress": 0..1} | {"log": "..."}
output.json: natija (lab/engines/results.py dagi sxema)

Modellashtirish (ns-3 3.40+):
  * host/server/router/sdnrouter -> InternetStack'li Node (hostlarda IpForward=false)
  * switch/p4switch               -> BridgeNetDevice'li Node (L2 learning)
  * L3<->L3 kabel -> PointToPoint; switch ishtirok etsa -> SimpleNetDevice/SimpleChannel
    (full-duplex, bridge'lanadi; CSMA half-duplex bo'lgani uchun ishlatilmaydi)
  * kabel: DataRate=bw, Delay=delay, DropTail navbat=queue paket, RateErrorModel=loss
  * routing: interfeys IP'lari + statik marshrutlar/gateway (yuqori ustuvorlik)
    + Ipv4GlobalRouting (SPF; OSPF/IS-IS ekvivalenti — dinamik daemonli routerlar uchun)
  * jitter ns-3 kanal modelida yo'q — e'tiborsiz qoldiriladi (logda aytiladi)
  * hodisalar (link_down/link_up/link_params) qadamlar orasida qo'llanadi,
    link_down'dan keyin global routing qayta hisoblanadi (konvergensiya ~0s)
"""

import ipaddress
import json
import math
import sys
import time

L3 = {"host", "server", "router", "sdnrouter"}
L2 = {"switch", "p4switch"}


def emit(**kw):
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


def rate_str(v):
    """'5Mbps' | 5 (Mbit/s) | '500kbps' -> ns-3 DataRate satri."""
    if isinstance(v, (int, float)):
        return f"{v}Mbps"
    s = str(v).strip().replace(" ", "")
    if s.replace(".", "", 1).isdigit():
        return s + "Mbps"
    return s.replace("Mbit/s", "Mbps").replace("kbit/s", "kbps")


def time_str(v):
    s = str(v).strip()
    if s.replace(".", "", 1).isdigit():
        return s + "ms"
    return s


def main(inp_path, out_path):
    with open(inp_path) as f:
        inp = json.load(f)
    project, sc = inp["project"], inp["scenario"]
    duration = float(sc.get("duration", 30))
    t_wall = time.time()
    emit(log="ns-3 yuklanmoqda...")
    from ns import ns  # noqa: E402  (og'ir import — faqat shu jarayonda)
    emit(log="ns-3 tayyor, topologiya qurilmoqda")
    ns.RngSeedManager.SetSeed(int(sc.get("seed", 1)) or 1)
    # TCP: Linux (Mininet) ga yaqin standartlar — ns-3 o'zi MSS=536, bufer=128KB
    # ishlatadi, bu oyna bilan o'tkazuvchanlikni sun'iy cheklaydi va dvigatellar
    # natijalarini solishtirib bo'lmas qiladi.
    ns.Config.SetDefault("ns3::TcpSocket::SegmentSize", ns.UintegerValue(1448))
    ns.Config.SetDefault("ns3::TcpSocket::SndBufSize", ns.UintegerValue(4 * 1024 * 1024))
    ns.Config.SetDefault("ns3::TcpSocket::RcvBufSize", ns.UintegerValue(6 * 1024 * 1024))
    ns.Config.SetDefault("ns3::TcpL4Protocol::SocketType", ns.StringValue("ns3::TcpCubic"))

    devs = project["devices"]
    nodes = {}
    for name in sorted(devs, key=lambda n: devs[n]["id"]):
        nodes[name] = ns.CreateObject[ns.Node]()
    # Routing: ripd yoqilgan (va ospfd/isisd yoqilmagan) routerlar — ns-3 RIPv2 (haqiqiy
    # protokol: periodik/triggered update, split horizon, timeout). Qolgan L3 qurilmalar —
    # static + Ipv4GlobalRouting (SPF; OSPF/IS-IS ekvivalenti, konvergensiya ~0s).
    def uses_rip(d):
        dm = set((d.get("config") or {}).get("daemons") or [])
        return d["type"] == "router" and "ripd" in dm and not (dm & {"ospfd", "isisd", "ospf6d"})

    rip_nodes = [n for n, d in devs.items() if uses_rip(d)]
    stack = ns.InternetStackHelper()
    rip_stack = None
    if rip_nodes:
        rip = ns.RipHelper()
        lst = ns.Ipv4ListRoutingHelper()
        lst.Add(ns.Ipv4StaticRoutingHelper(), 0)
        lst.Add(rip, -10)
        rip_stack = ns.InternetStackHelper()
        rip_stack.SetRoutingHelper(lst)
        emit(log=f"RIPv2 (ns-3): {', '.join(sorted(rip_nodes))}")
    for name, d in devs.items():
        if d["type"] in L3:
            (rip_stack if name in rip_nodes else stack).Install(nodes[name])

    # Eslatma: ns-3'da mavjud bo'lmagan atributga SetAttribute — C++ abort (Python
    # istisnosi emas). Noaniq atributlar uchun doim SetAttributeFailSafe.

    port_dev = {}          # (dev, port) -> NetDevice
    link_objs = {}         # lid -> {"devs": [d1, d2], "errs": [e1, e2], "ends": [...], "chan": ch}
    sw_ports = {n: ns.NetDeviceContainer() for n, d in devs.items() if d["type"] in L2}
    jitter_warn = False
    for lid, l in project["links"].items():
        a, b = l["a"], l["b"]
        p = l.get("params") or {}
        rate, delay = rate_str(p.get("bw", 100)), time_str(p.get("delay", "1ms"))
        q = f"{int(p.get('queue', 1000))}p"
        if str(p.get("jitter", "0ms")) not in ("0ms", "0", "0us", "0s", ""):
            jitter_warn = True
        pair = ns.NodeContainer()
        pair.Add(nodes[a["dev"]]); pair.Add(nodes[b["dev"]])
        if devs[a["dev"]]["type"] in L3 and devs[b["dev"]]["type"] in L3:
            h = ns.PointToPointHelper()
            h.SetDeviceAttribute("DataRate", ns.StringValue(rate))
            h.SetChannelAttribute("Delay", ns.StringValue(delay))
        else:
            # Switch kabeli: SimpleNetDevice + SimpleChannel — full-duplex, DataRate/Delay/
            # navbat/error model bor va BridgeNetDevice'ga ulanadi (SendFrom). CSMA
            # half-duplex va har freym uchun tarqalish vaqtini band qiladi — ms
            # darajadagi kechikishda o'tkazuvchanlikni sun'iy cheklaydi (2ms -> ~3.8Mbit/s).
            h = ns.SimpleNetDeviceHelper()
            h.SetDeviceAttribute("DataRate", ns.DataRateValue(ns.DataRate(rate)))
            h.SetChannelAttribute("Delay", ns.TimeValue(ns.Time(delay)))
            h.SetNetDevicePointToPointMode(False)
        try:
            h.SetQueue("ns3::DropTailQueue<Packet>", "MaxSize", ns.QueueSizeValue(ns.QueueSize(q)))
        except Exception:  # noqa: BLE001
            pass
        nd = h.Install(pair)
        d1, d2 = nd.Get(0), nd.Get(1)
        errs = []
        for dv in (d1, d2):
            em = ns.CreateObject[ns.RateErrorModel]()
            em.SetAttribute("ErrorUnit", ns.StringValue("ERROR_UNIT_PACKET"))
            em.SetAttribute("ErrorRate", ns.DoubleValue(float(p.get("loss", 0)) / 100.0))
            dv.SetAttribute("ReceiveErrorModel", ns.PointerValue(em))
            errs.append(em)
        port_dev[(a["dev"], a["port"])] = d1
        port_dev[(b["dev"], b["port"])] = d2
        for side, dv in ((a, d1), (b, d2)):
            if side["dev"] in sw_ports:
                sw_ports[side["dev"]].Add(dv)
        link_objs[lid] = {"devs": [d1, d2], "errs": errs, "ends": [a, b], "loss": float(p.get("loss", 0)) / 100.0,
                          "chan": d1.GetChannel()}
    if jitter_warn:
        emit(log="eslatma: jitter ns-3 kanal modelida yo'q — e'tiborsiz qoldirildi")

    emit(log=f"kabellar: {len(link_objs)}")
    bridge = ns.BridgeHelper()
    for n, cont in sw_ports.items():
        if cont.GetN():
            bridge.Install(nodes[n], cont)

    # IP manzillar
    emit(log="IP manzillar")
    iface = {}        # (dev, port) -> (ipv4, idx, IPv4Interface)
    for (dname, port), dv in port_dev.items():
        d = devs[dname]
        if d["type"] not in L3:
            continue
        ipv4 = nodes[dname].GetObject[ns.Ipv4]()
        idx = ipv4.AddInterface(dv)
        ip = (((d.get("config") or {}).get("interfaces") or {}).get(f"{dname}-eth{port}") or {}).get("ip")
        if ip:
            ii = ipaddress.ip_interface(ip)
            ipv4.AddAddress(idx, ns.Ipv4InterfaceAddress(ns.Ipv4Address(str(ii.ip)), ns.Ipv4Mask(str(ii.netmask))))
            iface[(dname, port)] = (ipv4, idx, ii)
        ipv4.SetUp(idx)
    # Hostlar tranzit bo'lmasin. Lekin ns-3 Ipv4GlobalRouting P2P bilan ulangan,
    # IpForward=false bo'lgan hostda segfault beradi (ns-3.44). Bitta interfeysli host
    # baribir tranzit bo'la olmaydi — faqat ko'p interfeyslilarda o'chiramiz.
    for name, d in devs.items():
        if d["type"] in ("host", "server") and sum(1 for (dn, _p) in iface if dn == name) > 1:
            nodes[name].GetObject[ns.Ipv4]().SetAttribute("IpForward", ns.BooleanValue(False))

    # statik marshrutlar + gateway (Ipv4StaticRouting ustuvorligi global'dan yuqori)
    srh = ns.Ipv4StaticRoutingHelper()

    def out_if(dname, via):
        for (dn, _p), (_ipv4, idx, ii) in iface.items():
            if dn == dname and via in ii.network:
                return idx
        return None

    for name, d in devs.items():
        if d["type"] not in L3:
            continue
        cfg = d.get("config") or {}
        sr = srh.GetStaticRouting(nodes[name].GetObject[ns.Ipv4]())
        routes = list(cfg.get("routes") or [])
        if cfg.get("gateway") and d["type"] in ("host", "server"):
            routes.append({"prefix": "0.0.0.0/0", "via": cfg["gateway"]})
        for r in routes:
            try:
                net = ipaddress.ip_network(r["prefix"], strict=False)
                via = ipaddress.ip_address(r["via"])
            except (KeyError, ValueError):
                continue
            idx = out_if(name, via)
            if idx is None:
                emit(log=f"{name}: {r['via']} ulangan subnetda emas — marshrut o'tkazib yuborildi")
                continue
            sr.AddNetworkRouteTo(ns.Ipv4Address(str(net.network_address)), ns.Ipv4Mask(str(net.netmask)),
                                 ns.Ipv4Address(str(via)), idx)
    emit(log="routing jadvallari (global SPF)")
    ns.Ipv4GlobalRoutingHelper.PopulateRoutingTables()
    emit(log="ilovalar")

    # ilovalar
    def ip_of(dname, explicit=None):
        if explicit:
            return explicit
        for (dn, _p), (_i, _x, ii) in sorted(iface.items()):
            if dn == dname:
                return str(ii.ip)
        return None

    apps = []
    for i, app in enumerate(sc.get("apps") or []):
        src, dst = app.get("src"), app.get("dst")
        if src not in nodes or dst not in nodes:
            emit(log=f"ilova {app.get('id')}: qurilma topilmadi")
            continue
        dip = ip_of(dst, app.get("dst_ip"))
        if not dip:
            emit(log=f"ilova {app.get('id')}: {dst} da IP yo'q")
            continue
        port = 9000 + i
        start, stop = float(app.get("start", 1)), float(app.get("stop", duration))
        typ = app.get("type", "udp")
        addr = ns.InetSocketAddress(ns.Ipv4Address(dip), port).ConvertTo()
        if typ == "ping":
            srv = ns.UdpEchoServerHelper(port)
            sa = srv.Install(nodes[dst]); sa.Start(ns.Seconds(0.0))
            cl = ns.UdpEchoClientHelper(ns.Ipv4Address(dip).ConvertTo(), port)
            cl.SetAttribute("MaxPackets", ns.UintegerValue(10 ** 7))
            cl.SetAttribute("Interval", ns.TimeValue(ns.Seconds(float(app.get("interval", 0.2)))))
            cl.SetAttribute("PacketSize", ns.UintegerValue(int(app.get("size", 64))))
            ca = cl.Install(nodes[src])
        else:
            proto = "ns3::TcpSocketFactory" if typ == "tcp" else "ns3::UdpSocketFactory"
            sink = ns.PacketSinkHelper(proto, ns.InetSocketAddress(ns.Ipv4Address.GetAny(), port).ConvertTo())
            sa = sink.Install(nodes[dst]); sa.Start(ns.Seconds(0.0))
            if typ == "tcp" and not app.get("rate"):
                bs = ns.BulkSendHelper(proto, addr)
                bs.SetAttribute("MaxBytes", ns.UintegerValue(0))
                ca = bs.Install(nodes[src])
            else:
                oo = ns.OnOffHelper(proto, addr)
                oo.SetConstantRate(ns.DataRate(rate_str(app.get("rate", "1Mbps"))), int(app.get("size", 1000)))
                ca = oo.Install(nodes[src])
        ca.Start(ns.Seconds(start)); ca.Stop(ns.Seconds(stop))
        apps.append({"id": app.get("id") or f"a{i + 1}", "type": typ, "src": src, "dst": dst, "port": port,
                     "label": app.get("label") or f"{typ} {src}→{dst}",
                     "start": start, "stop": min(stop, duration)})

    fmh = ns.FlowMonitorHelper()
    mon = fmh.InstallAll()
    clf = ns.DynamicCast[ns.Ipv4FlowClassifier](fmh.GetClassifier())

    # hodisalar
    def set_link(lid, up=None, params=None):
        lo = link_objs.get(lid)
        if not lo:
            return
        if params:
            if "bw" in params:
                r = ns.StringValue(rate_str(params["bw"]))
                for dv in lo["devs"]:
                    if not dv.SetAttributeFailSafe("DataRate", r):   # CSMA: DataRate kanalda
                        lo["chan"].SetAttributeFailSafe("DataRate", r)
            if "delay" in params:
                lo["chan"].SetAttributeFailSafe("Delay", ns.StringValue(time_str(params["delay"])))
            if "loss" in params:
                lo["loss"] = float(params["loss"]) / 100.0
                for em in lo["errs"]:
                    em.SetAttribute("ErrorRate", ns.DoubleValue(lo["loss"]))
        if up is not None:
            for em in lo["errs"]:
                em.SetAttribute("ErrorRate", ns.DoubleValue(lo["loss"] if up else 1.0))
            for end in lo["ends"]:
                k = (end["dev"], end["port"])
                if k in iface:
                    ipv4, idx, _ = iface[k]
                    (ipv4.SetUp if up else ipv4.SetDown)(idx)
            ns.Ipv4GlobalRoutingHelper.RecomputeRoutingTables()

    events = sorted(sc.get("events") or [], key=lambda e: float(e.get("t", 0)))
    initially_down = [lid for lid, l in project["links"].items() if l.get("up") is False]
    for lid in initially_down:
        set_link(lid, up=False)

    # qadamli ijro: har 1s (va har hodisa vaqtida) to'xtab, FlowMonitor o'lchanadi
    step = float(sc.get("sample", 1.0))
    marks = sorted({round(k * step, 6) for k in range(1, int(math.ceil(duration / step)) + 1)}
                   | {round(float(e["t"]), 6) for e in events if 0 < float(e.get("t", 0)) < duration})
    prev = {}
    series = {a["id"]: [] for a in apps}
    now = 0.0
    ev_i = 0
    applied = []

    def flows_for(app):
        fwd, rev = [], []
        for fid, st in mon.GetFlowStats():
            t5 = clf.FindFlow(fid)
            if t5.destinationPort == app["port"]:
                fwd.append((fid, st))
            elif t5.sourcePort == app["port"]:
                rev.append((fid, st))
        return fwd, rev

    def agg(fl):
        tx = sum(s.txPackets for _f, s in fl); rx = sum(s.rxPackets for _f, s in fl)
        rxb = sum(s.rxBytes for _f, s in fl)
        dl = sum(s.delaySum.GetSeconds() for _f, s in fl); jt = sum(s.jitterSum.GetSeconds() for _f, s in fl)
        return tx, rx, rxb, dl, jt

    for mark in marks:
        while ev_i < len(events) and float(events[ev_i].get("t", 0)) <= now + 1e-9:
            e = events[ev_i]; ev_i += 1
            act = e.get("action")
            set_link(e.get("link"), up=(act == "link_up") if act in ("link_up", "link_down") else None,
                     params=e.get("params") if act == "link_params" else None)
            applied.append({"t": now, **e})
            emit(log=f"t={now:.1f}s: {act} {e.get('link')}")
        ns.Simulator.Stop(ns.Seconds(mark - now))
        ns.Simulator.Run()
        dt = mark - now
        now = mark
        if abs(mark / step - round(mark / step)) > 1e-6:
            continue   # hodisa nuqtasi — namuna olinmaydi
        mon.CheckForLostPackets()
        for app in apps:
            fwd, rev = flows_for(app)
            cur = (agg(fwd), agg(rev))
            p0 = prev.get(app["id"], ((0, 0, 0, 0.0, 0.0), (0, 0, 0, 0.0, 0.0)))
            prev[app["id"]] = cur
            (tx, rx, rxb, dl, jt), (rtx, rrx, _rb, rdl, _rj) = cur
            (tx0, rx0, rxb0, dl0, jt0), (rtx0, rrx0, _rb0, rdl0, _rj0) = p0
            drx, dtx = rx - rx0, tx - tx0
            pt = {"t": round(now, 3), "thr_bps": round((rxb - rxb0) * 8 / max(dt, 1e-9)),
                  "delay_ms": round((dl - dl0) / drx * 1000, 3) if drx > 0 else None,
                  "jitter_ms": round((jt - jt0) / (drx - 1) * 1000, 3) if drx > 1 else None,
                  "loss_pct": round(max(dtx - drx, 0) / dtx * 100, 2) if dtx > 0 else None}
            if app["type"] == "ping":
                drr = rrx - rrx0
                pt["rtt_ms"] = (round(pt["delay_ms"] + (rdl - rdl0) / drr * 1000, 3)
                                if pt["delay_ms"] is not None and drr > 0 else None)
            series[app["id"]].append(pt)
        emit(progress=round(now / duration, 3))

    # yakuniy xulosa
    flows = {}
    for app in apps:
        fwd, rev = flows_for(app)
        tx, rx, rxb, dl, jt = agg(fwd)
        _rtx, rrx, _rb, rdl, _rj = agg(rev)
        active = max(app["stop"] - app["start"], 1e-9)
        summ = {"tx_pkts": tx, "rx_pkts": rx, "rx_bytes": rxb,
                "avg_thr_bps": round(rxb * 8 / active),
                "avg_delay_ms": round(dl / rx * 1000, 3) if rx else None,
                "avg_jitter_ms": round(jt / (rx - 1) * 1000, 3) if rx > 1 else None,
                "loss_pct": round((tx - rx) / tx * 100, 2) if tx else None}
        if app["type"] == "ping" and rx and rrx:
            summ["avg_rtt_ms"] = round((dl / rx + rdl / rrx) * 1000, 3)
        flows[app["id"]] = {**app, "series": series[app["id"]], "summary": summ}
    ns.Simulator.Destroy()
    out = {"engine": "ns3", "engine_label": "ns-3 (diskret-hodisali simulyatsiya)",
           "duration": duration, "flows": flows, "events": applied,
           "wall_s": round(time.time() - t_wall, 2),
           "notes": ["dinamik routing: ripd → ns-3 RIPv2 (haqiqiy konvergensiya); ospfd/isisd va boshqalar → Ipv4GlobalRouting (SPF, darhol)",
                     "TCP: Cubic, MSS 1448, bufer 4/6 MB (Linux'ga yaqin)",
                     "jitter parametri modellashtirilmaydi", "P4/SDN mantiqi: switch L2 bridge, SDN router — L3 router sifatida"]}
    with open(out_path, "w") as f:
        json.dump(out, f)
    emit(progress=1.0, log=f"tugadi ({out['wall_s']}s)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
