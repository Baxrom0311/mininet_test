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
    + SPF (lab Net.routes — analitik bilan bir xil; OSPF/IS-IS ekvivalenti), RIP -> ns-3 RIPv2
  * jitter ns-3 kanal modelida yo'q — e'tiborsiz qoldiriladi (logda aytiladi)
  * hodisalar (link_down/link_up/link_params) qadamlar orasida qo'llanadi,
    OSPF/SPF marshrutlar qayta hisoblanadi (ixtiyoriy ospf_convergence_s kechikishi)
"""

import copy
import ipaddress
import json
import math
import os
import sys
import time

L3 = {"host", "server", "router", "sdnrouter"}
L2 = {"switch", "p4switch", "wifiap"}
WIFI_STD = {"80211g": "WIFI_STANDARD_80211g", "80211n": "WIFI_STANDARD_80211n",
            "80211ac": "WIFI_STANDARD_80211ac", "80211ax": "WIFI_STANDARD_80211ax"}


# Qurilma statistikasi uchun C++ yordamchi (cppyy): Ipv4L3Protocol trace'lari
# C++ ichida sanaladi (Python callback'lari har paketda juda sekin bo'lardi).
_NODESTATS_CPP = r"""
#include <sstream>
#include <cstdlib>
#include <iostream>
namespace netlab {
using namespace ns3;
// Hisoblagichlar node id bo'yicha vektorda (har paketda std::map qidiruvi yo'q). TX = mahalliy
// yuborilgan (SendOutgoing) + uzatilgan (UnicastForward, MulticastForward) — Linux OutRequests +
// ForwDatagrams kabi.
// "Tx" trace ishlatilmaydi: unga ulanilsa ns-3 har hop'da paketni nusxalab sarlavha qo'shadi.
struct Cnt { uint64_t fwd = 0, rx = 0, tx = 0, noroute = 0, drop = 0; };
static std::vector<Cnt> g_cnt;
static inline Cnt& C(uint32_t id) { if (id >= g_cnt.size()) g_cnt.resize(id + 1); return g_cnt[id]; }
static void OnFwd(Cnt* c, const Ipv4Header&, Ptr<const Packet>, uint32_t) { c->fwd++; c->tx++; }
static void OnOut(Cnt* c, const Ipv4Header&, Ptr<const Packet>, uint32_t) { c->tx++; }
static void OnRx(Cnt* c, Ptr<const Packet>, Ptr<Ipv4>, uint32_t) { c->rx++; }
static void OnDrop(Cnt* c, const Ipv4Header&, Ptr<const Packet>, Ipv4L3Protocol::DropReason r,
                   Ptr<Ipv4>, uint32_t) {
  if (r == Ipv4L3Protocol::DROP_NO_ROUTE) c->noroute++; else c->drop++;
}
void Reserve(uint32_t n) { g_cnt.reserve(n + 1); g_cnt.resize(n + 1); }
void Attach(Ptr<Node> n) {
  uint32_t id = n->GetId();
  Ptr<Ipv4L3Protocol> ip = n->GetObject<Ipv4L3Protocol>();
  if (!ip) return;
  Cnt* c = &C(id);   // Reserve() oldindan chaqiriladi — vektor qayta joylashmaydi
  ip->TraceConnectWithoutContext("UnicastForward", MakeBoundCallback(&OnFwd, c));
  ip->TraceConnectWithoutContext("SendOutgoing", MakeBoundCallback(&OnOut, c));
  ip->TraceConnectWithoutContext("MulticastForward", MakeBoundCallback(&OnOut, c));   // har chiqish interfeysi
  ip->TraceConnectWithoutContext("Rx", MakeBoundCallback(&OnRx, c));
  ip->TraceConnectWithoutContext("Drop", MakeBoundCallback(&OnDrop, c));
}
static void OnFwd6(Cnt* c, const Ipv6Header&, Ptr<const Packet>, uint32_t) { c->fwd++; c->tx++; }
static void OnOut6(Cnt* c, const Ipv6Header&, Ptr<const Packet>, uint32_t) { c->tx++; }
static void OnRx6(Cnt* c, Ptr<const Packet>, Ptr<Ipv6>, uint32_t) { c->rx++; }
static void OnDrop6(Cnt* c, const Ipv6Header&, Ptr<const Packet>, Ipv6L3Protocol::DropReason r,
                    Ptr<Ipv6>, uint32_t) {
  if (r == Ipv6L3Protocol::DROP_NO_ROUTE) c->noroute++; else c->drop++;
  static unsigned debugDrops = 0;
  if (std::getenv("NETLAB_NS3_DEBUG") && debugDrops++ < 5) std::cerr << "IPv6 drop reason=" << r << std::endl;
}
void Attach6(Ptr<Node> n) {
  Ptr<Ipv6L3Protocol> ip = n->GetObject<Ipv6L3Protocol>();
  if (!ip) return;
  Cnt* c = &C(n->GetId());
  ip->TraceConnectWithoutContext("UnicastForward", MakeBoundCallback(&OnFwd6, c));
  ip->TraceConnectWithoutContext("SendOutgoing", MakeBoundCallback(&OnOut6, c));
  ip->TraceConnectWithoutContext("Rx", MakeBoundCallback(&OnRx6, c));
  ip->TraceConnectWithoutContext("Drop", MakeBoundCallback(&OnDrop6, c));
}
// barcha tugunlar hisoblagichlari bitta chaqiruvda (Python<->C++ o'tishlari kamroq)
std::vector<uint64_t> GetAll(const std::vector<uint32_t>& ids) {
  std::vector<uint64_t> out;
  out.reserve(ids.size() * 5);
  for (uint32_t id : ids) { Cnt& c = C(id); out.insert(out.end(), {c.fwd, c.rx, c.tx, c.noroute, c.drop}); }
  return out;
}
// cheklangan uzatish (FTP/HTTP): PacketSink Rx -> maqsad baytga yetgan aniq vaqt
static std::map<uint32_t, std::pair<uint64_t, uint64_t>> g_sink;
static std::map<uint32_t, double> g_done;
static void OnSinkRx(uint32_t key, Ptr<const Packet> p, const Address&) {
  auto& e = g_sink[key];
  e.first += p->GetSize();
  if (e.first >= e.second && !g_done.count(key)) g_done[key] = Simulator::Now().GetSeconds();
}
void WatchSink(Ptr<Application> a, uint32_t key, uint64_t target) {
  g_sink[key] = {0, target};
  a->TraceConnectWithoutContext("Rx", MakeBoundCallback(&OnSinkRx, key));
}
double DoneAt(uint32_t key) {
  auto it = g_done.find(key);
  return it == g_done.end() ? -1.0 : it->second;
}
// multicast qabul qiluvchilar: SeqTsSizeHeader (yuborilgan vaqt, seq) bo'yicha bayt/paket/kechikish
struct Mc { double bytes = 0, pkts = 0, dsum = 0, jsum = 0, last = -1; };
static std::map<uint32_t, Mc> g_mc;
static std::map<uint32_t, double> g_mctx;
static void OnMcRx(uint32_t key, Ptr<const Packet> p, const Address&, const Address&, const SeqTsSizeHeader& h) {
  Mc& m = g_mc[key];
  double d = (Simulator::Now() - h.GetTs()).GetSeconds();
  m.bytes += p->GetSize(); m.pkts += 1; m.dsum += d;
  if (m.last >= 0) m.jsum += std::abs(d - m.last);
  m.last = d;
}
static void OnMcTx(uint32_t key, Ptr<const Packet>) { g_mctx[key] += 1; }
void WatchMc(Ptr<Application> sink, uint32_t key) {
  g_mc[key];
  sink->TraceConnectWithoutContext("RxWithSeqTsSize", MakeBoundCallback(&OnMcRx, key));
}
void WatchMcTx(Ptr<Application> src, uint32_t key) {
  g_mctx[key] = 0;
  src->TraceConnectWithoutContext("Tx", MakeBoundCallback(&OnMcTx, key));
}
std::vector<double> McGet(uint32_t key, uint32_t gkey) {
  Mc& m = g_mc[key];
  return {m.bytes, m.pkts, m.dsum, m.jsum, g_mctx[gkey]};
}
std::vector<uint64_t> Get(uint32_t id) {
  Cnt& c = C(id);
  return {c.fwd, c.rx, c.tx, c.noroute, c.drop};
}
std::string Routes(Ptr<Node> n) {
  std::ostringstream os;
  Ptr<OutputStreamWrapper> w = Create<OutputStreamWrapper>(&os);
  Ptr<Ipv4> ip = n->GetObject<Ipv4>();
  if (ip && ip->GetRoutingProtocol()) ip->GetRoutingProtocol()->PrintRoutingTable(w, Time::S);
  return os.str();
}
}
"""


def parse_ns3_routes(text, idx_ifn):
    """PrintRoutingTable matni -> {route_key} (Linux /proc/net/route bilan bir xil kalitlar)."""
    from lab.engines.nodestats import route_key
    out = set()
    for line in text.splitlines():
        v = line.split()
        if len(v) < 8 or v[0].count(".") != 3 or not v[0][0].isdigit():
            continue
        try:
            net = ipaddress.ip_network(f"{v[0]}/{v[2]}", strict=False)
        except ValueError:
            continue
        if net.is_loopback or net.prefixlen == 32:
            continue
        ifn = idx_ifn.get(int(v[-1]), f"if{v[-1]}") if v[-1].isdigit() else v[-1]
        out.add(route_key(net, None if v[1] == "0.0.0.0" else v[1], ifn))
    return out


def _set_err(em, rate):
    """RateErrorModel: rate=0 da o'chiriladi (har paketda tasodifiy son chizilmaydi; natija bir xil —
    har model o'z RNG oqimiga ega)."""
    from ns import ns
    em.SetAttribute("ErrorRate", ns.DoubleValue(float(rate)))
    if float(rate) > 0:
        em.Enable()
    else:
        em.Disable()


def emit(**kw):
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


def rate_str(v):
    """Canonical integer bit/s, including numeric values printed in exponent form."""
    from lab.model import parse_rate
    return f"{int(parse_rate(v))}bps"


def time_str(v):
    s = str(v).strip()
    if s.replace(".", "", 1).isdigit():
        return s + "ms"
    return s


def main(inp_path, out_path):
    with open(inp_path) as f:
        inp = json.load(f)
    project, sc = inp["project"], inp["scenario"]
    from lab.addressing import project_for_family
    version = sc.get("ip_version", 4)
    project = project_for_family(project, version)
    from lab.services import resolve_dhcp
    project, _leases = resolve_dhcp(project)   # DHCP: ns-3 da lizing manzillari statik o'rnatiladi
    duration = float(sc.get("duration", 30))
    t_wall = time.time()
    emit(log="ns-3 yuklanmoqda...")
    from ns import ns  # noqa: E402  (og'ir import — faqat shu jarayonda)
    emit(log="ns-3 tayyor, topologiya qurilmoqda")
    from lab.engines.ns3_addressing import AddressFamily
    family = AddressFamily(ns, version)
    for comp in filter(None, os.environ.get("NETLAB_NS3_LOG", "").split(",")):   # debug: ns-3 loglari (stderr)
        ns.LogComponentEnable(comp.strip(), ns.LOG_LEVEL_INFO)
    ns.RngSeedManager.SetSeed(int(sc.get("seed", 1)) or 1)
    ns.RngSeedManager.SetRun(int(sc.get("rng_run", 1)))
    # TCP: Linux (Mininet) ga yaqin standartlar — ns-3 o'zi MSS=536, bufer=128KB
    # ishlatadi, bu oyna bilan o'tkazuvchanlikni sun'iy cheklaydi va dvigatellar
    # natijalarini solishtirib bo'lmas qiladi.
    # Tezkor rejim (ixtiyoriy, `ns3_fast: true` yoki NETLAB_NS3_FAST=1): jumbo MTU 9000 / MSS 8948 —
    # TCP paketlari ~6x kam (ns-3 vaqti paketlar soniga proporsional). Navbatlar BAYT bo'yicha
    # teng qoldiriladi (paket soni x1500/9000). Natija TAXMINIY: o'tkazuvchanlik deyarli bir xil,
    # paket darajasidagi kechikish/jitter va kichik navbatlarda yo'qotish farq qilishi mumkin.
    # Wi-Fi bo'lsa qo'llanmaydi (MSDU 2304 — fragmentatsiya bo'lardi).
    fast = bool(sc.get("ns3_fast")) or os.environ.get("NETLAB_NS3_FAST") == "1"
    if fast and any((l.get("params") or {}).get("medium") == "wifi" for l in project["links"].values()):
        emit(log="ns3_fast: Wi-Fi kabellari bor — tezkor rejim qo'llanmadi")
        fast = False
    mtu = 9000 if fast else 1500
    if fast:
        emit(log=f"ns3_fast: MTU 9000 / MSS {mtu - (72 if version == 6 else 52)}, navbatlar bayt bo'yicha teng (taxminiy rejim)")
    ns.Config.SetDefault("ns3::TcpSocket::SegmentSize", ns.UintegerValue(mtu - (72 if version == 6 else 52)))
    ns.Config.SetDefault("ns3::TcpSocket::SndBufSize", ns.UintegerValue(4 * 1024 * 1024))
    ns.Config.SetDefault("ns3::TcpSocket::RcvBufSize", ns.UintegerValue(6 * 1024 * 1024))
    ns.Config.SetDefault("ns3::TcpL4Protocol::SocketType", ns.StringValue("ns3::TcpCubic"))

    devs = project["devices"]
    if _leases:
        emit(log=f"DHCP: {len(_leases)} lizing (statik ekvivalent): "
                 + ", ".join(f"{x['client']}={x['ip']}" for x in _leases[:6]))
    if any("bgpd" in ((d.get("config") or {}).get("daemons") or []) for d in devs.values()):
        emit(log="eslatma: BGP ns-3 da yo'q — model (lab/bgp.py) hisoblagan BGP marshrutlari statik o'rnatiladi "
                 "(hodisada darhol qayta hisoblanadi)")
    if any((d.get("config") or {}).get("nat") for d in devs.values()):
        emit(log="eslatma: NAT ns-3 da yo'q — manba manzil almashtirilmaydi (ichki subnet marshruti bo'lmasa javob yetmaydi)")
    nodes = {}
    for name in sorted(devs, key=lambda n: devs[n]["id"]):
        nodes[name] = ns.CreateObject[ns.Node]()
    # Routing: ripd yoqilgan (va ospfd/isisd yoqilmagan) routerlar — ns-3 RIPv2 (haqiqiy
    # protokol: periodik/triggered update, split horizon, timeout). Qolgan L3 qurilmalar —
    # static + SPF marshrutlar (OSPF uchun ixtiyoriy ospf_convergence_s vaqt modeli).
    def uses_rip(d):
        dm = set((d.get("config") or {}).get("daemons") or [])
        return d["type"] == "router" and "ripd" in dm and not (dm & {"ospfd", "isisd", "ospf6d"})

    rip_nodes = [n for n, d in devs.items() if uses_rip(d)]
    # Ipv4GlobalRouting ISHLATILMAYDI: ns-3.44 da u (1) P2P bilan ulangan IpForward=false
    # hostda segfault beradi, (2) bir nechta AP umumiy Wi-Fi kanaliga va bitta LAN'ga
    # bridge qilinganda "L2 forwarding loop" deb to'xtaydi. Marshrutlarni analitik
    # dvigatel bilan BIR XIL model (Net.routes) hisoblaydi va statik sifatida o'rnatadi.
    stack = ns.InternetStackHelper()
    _lst_static = ns.Ipv4ListRoutingHelper()
    _lst_static.Add(ns.Ipv4StaticRoutingHelper(), 0)
    stack.SetRoutingHelper(_lst_static)
    family.configure_stack(stack)
    # RIP taymerlari (config.rip_timers / startup `timers basic`) — har xil taymerli
    # routerlar guruhi uchun alohida RipHelper (atributlar helper darajasida).
    from lab.model import rip_timers
    rip_stacks, _keep = {}, []
    for n in rip_nodes:
        tm = rip_timers(devs[n])
        key = (tm["update"], tm["timeout"], tm["garbage"])
        if key not in rip_stacks:
            rip = ns.RipHelper()
            rip.Set("UnsolicitedRoutingUpdate", ns.TimeValue(ns.Seconds(key[0])))
            rip.Set("TimeoutDelay", ns.TimeValue(ns.Seconds(key[1])))
            rip.Set("GarbageCollectionDelay", ns.TimeValue(ns.Seconds(key[2])))
            lst = ns.Ipv4ListRoutingHelper()
            lst.Add(ns.Ipv4StaticRoutingHelper(), 0)
            lst.Add(rip, -10)
            st_ = ns.InternetStackHelper()
            st_.SetRoutingHelper(lst)
            family.configure_stack(st_)
            rip_stacks[key] = st_
            _keep += [rip, lst]
    if rip_nodes:
        emit(log=f"RIPv2 (ns-3): {', '.join(sorted(rip_nodes))}; taymerlar (update/timeout/garbage): "
                 f"{', '.join('/'.join(map(str, k)) for k in rip_stacks)}")
    for name, d in devs.items():
        if d["type"] in L3:
            if name in rip_nodes:
                tm = rip_timers(d)
                rip_stacks[(tm["update"], tm["timeout"], tm["garbage"])].Install(nodes[name])
            else:
                stack.Install(nodes[name])
    import cppyy
    cppyy.cppdef(_NODESTATS_CPP)
    nl = cppyy.gbl.netlab
    from lab.engines.nodestats import L3_NS, RouteTracker, convergence
    stat_nodes = [n for n, d in devs.items() if d["type"] in L3_NS]
    nl.Reserve(max(nd.GetId() for nd in nodes.values()))
    for n in stat_nodes:
        (nl.Attach6 if version == 6 else nl.Attach)(nodes[n])

    # Eslatma: ns-3'da mavjud bo'lmagan atributga SetAttribute — C++ abort (Python
    # istisnosi emas). Noaniq atributlar uchun doim SetAttributeFailSafe.

    port_dev = {}          # (dev, port) -> NetDevice
    link_objs = {}         # lid -> {"devs": [d1, d2], "errs": [e1, e2], "ends": [...], "chan": ch}
    # VLAN: ns-3 BridgeNetDevice 802.1Q tegini bilmaydi — access portlar har VLAN uchun
    # alohida bridge bilan modellanadi; trunk/subinterfeys qo'llab-quvvatlanmaydi.
    from lab.model import parse_ifname, switch_port_cfg
    for n, d in devs.items():
        for pk in ((d.get("config") or {}).get("ports") or {}):
            if switch_port_cfg(d, int(pk))["mode"] == "trunk":
                raise RuntimeError(f"ns-3: {n} port {pk} — VLAN trunk ns-3 dvigatelida qo'llab-quvvatlanmaydi "
                                   "(analitik yoki Mininet dvigatelidan foydalaning)")
        for ifn in ((d.get("config") or {}).get("interfaces") or {}):
            if parse_ifname(ifn)[2] is not None:
                raise RuntimeError(f"ns-3: {n}/{ifn} — VLAN subinterfeys ns-3 dvigatelida qo'llab-quvvatlanmaydi")

    def port_vlan(dev, port):
        pc = switch_port_cfg(devs[dev], port)
        return pc["vlan"] if pc["mode"] == "access" else 0
    sw_ports = {n: {} for n, d in devs.items() if d["type"] in L2}
    # STP: ns-3 BridgeNetDevice'da spanning tree yo'q — model daraxti (lab.stp) bo'yicha
    # blocking portlar bridge'ga ulanmaydi; STP'siz halqa — ns-3 cheksiz aylanadi -> xato.
    from lab.stp import compute as stp_compute
    _st = stp_compute(project)
    if _st["storm"]:
        sw_ = sorted(_st["storm"])[0]
        raise RuntimeError(f"ns-3: L2 halqa (STP o'chiq) — broadcast storm: {', '.join(sorted(_st['storm']))} "
                           f"[{', '.join(_st['storm_cycle'].get(sw_, []))}]. Switch'larda stp: true qiling")
    if _st["blocked"]:
        emit(log="STP (model daraxti): blocking portlar " + ", ".join(f"{a}:{b}" for a, b in sorted(_st["blocked"]))
                 + " — blocking segment ma'lumot uchun yopiladi (ErrorModel=1), hodisalarda daraxt "
                   "qayta hisoblanadi (802.1D 30/50 s, RSTP ~1 s)")
    jitter_warn = False
    wifi_links = []
    port_q = {}       # (dev, port) -> navbat hajmi ("1000p") — L3 tomonda qdisc uchun
    port_qd = {}      # (dev, port) -> fifo | prio | fq_codel
    for lid, l in project["links"].items():
        a, b = l["a"], l["b"]
        p = l.get("params") or {}
        if p.get("medium") == "wifi":
            wifi_links.append((lid, l))
            continue
        rate, delay = rate_str(p.get("bw", 100)), time_str(p.get("delay", "1ms"))
        q = f"{max(10, round(int(p.get('queue', 1000)) * 1500 / mtu))}p"
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
        if fast:
            for dv in (d1, d2):
                dv.SetMtu(mtu)
        errs = []
        for dv in (d1, d2):
            em = ns.CreateObject[ns.RateErrorModel]()
            em.SetAttribute("ErrorUnit", ns.StringValue("ERROR_UNIT_PACKET"))
            _set_err(em, float(p.get("loss", 0)) / 100.0)
            dv.SetAttribute("ReceiveErrorModel", ns.PointerValue(em))
            errs.append(em)
        port_dev[(a["dev"], a["port"])] = d1
        port_dev[(b["dev"], b["port"])] = d2
        port_q[(a["dev"], a["port"])] = port_q[(b["dev"], b["port"])] = q
        port_qd[(a["dev"], a["port"])] = port_qd[(b["dev"], b["port"])] = p.get("qdisc") or "fifo"
        for side, dv in ((a, d1), (b, d2)):
            if side["dev"] in sw_ports:
                sw_ports[side["dev"]].setdefault(port_vlan(side["dev"], side["port"]), ns.NetDeviceContainer()).Add(dv)
        link_objs[lid] = {"devs": [d1, d2], "errs": errs, "ends": [a, b], "loss": float(p.get("loss", 0)) / 100.0,
                          "chan": d1.GetChannel()}
    if jitter_warn:
        emit(log="eslatma: jitter ns-3 kanal modelida yo'q — e'tiborsiz qoldirildi")

    # ---- Wi-Fi: har diapazon uchun BITTA umumiy YansWifiChannel (LogDistance n=3) —
    # bir kanaldagi AP'lar va ularning stansiyalari bir-biriga haqiqatan xalaqit beradi
    # (CSMA/CA, to'qnashuv), boshqa kanaldagilar PHY darajasida ajratiladi. IdealWifiManager,
    # joylashuv = kanvas koordinatasi * meters_per_px. Model lab/wifi.py bilan bir xil.
    shared_ch = {}
    placed = set()          # mobility o'rnatilgan (Wi-Fi ishtirokchi) qurilmalar
    ap_macs = []            # (Mac48Address, AP nomi) — roaming kuzatuvi uchun
    sta_macs = {}           # stansiya -> (StaWifiMac, kabel id, boshlang'ich AP)
    if wifi_links:
        from lab.wifi import REF_LOSS, ap_config, meters_per_px
        mpp = meters_per_px(project)
        by_ap = {}
        for lid, l in wifi_links:
            ap_end, sta_end = (l["a"], l["b"]) if devs[l["a"]["dev"]]["type"] == "wifiap" else (l["b"], l["a"])
            by_ap.setdefault(ap_end["dev"], []).append((lid, ap_end, sta_end))
        for n_, d_ in devs.items():      # stansiyasiz AP'lar ham beacon yuborsin (roaming uchun)
            if d_["type"] == "wifiap":
                by_ap.setdefault(n_, [])
        mob_nodes, mob_pos = ns.NodeContainer(), ns.ListPositionAllocator()

        def place(n):
            if n in placed:
                return
            placed.add(n)
            mob_nodes.Add(nodes[n])
            mob_pos.Add(ns.Vector(float(devs[n].get("x", 0)) * mpp, float(devs[n].get("y", 0)) * mpp, 1.5))

        for ap, members in by_ap.items():
            cfg = ap_config(devs[ap])
            if cfg["band"] not in shared_ch:
                ch = ns.YansWifiChannelHelper()
                ch.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel")
                ch.AddPropagationLoss("ns3::LogDistancePropagationLossModel", "Exponent", ns.DoubleValue(3.0),
                                      "ReferenceLoss", ns.DoubleValue(REF_LOSS[cfg["band"]]))
                shared_ch[cfg["band"]] = ch.Create()
            phy = ns.YansWifiPhyHelper()
            phy.SetChannel(shared_ch[cfg["band"]])
            band = "BAND_5GHZ" if cfg["band"] == 5 else "BAND_2_4GHZ"
            phy.Set("ChannelSettings", ns.StringValue(f"{{{cfg['channel']}, {cfg['width']}, {band}, 0}}"))
            phy.Set("TxPowerStart", ns.DoubleValue(cfg["tx_power"]))
            phy.Set("TxPowerEnd", ns.DoubleValue(cfg["tx_power"]))
            wifi = ns.WifiHelper()
            wifi.SetStandard(getattr(ns, WIFI_STD[cfg["standard"]]))
            wifi.SetRemoteStationManager("ns3::IdealWifiManager")
            mac = ns.WifiMacHelper()
            ssid = ns.Ssid(cfg["ssid"] or f"netlab-{ap}")
            stas = ns.NodeContainer()
            for _lid, _ae, se in members:
                stas.Add(nodes[se["dev"]])
            mac.SetType("ns3::StaWifiMac", "Ssid", ns.SsidValue(ssid))
            sdevs = wifi.Install(phy, mac, stas) if members else None
            mac.SetType("ns3::ApWifiMac", "Ssid", ns.SsidValue(ssid))
            ap_dev = wifi.Install(phy, mac, ns.NodeContainer(nodes[ap])).Get(0)
            sw_ports[ap].setdefault(0, ns.NetDeviceContainer()).Add(ap_dev)
            ap_macs.append((ns.Mac48Address.ConvertFrom(ap_dev.GetAddress()), ap))
            place(ap)
            for i, (lid, ae, se) in enumerate(members):
                sd = sdevs.Get(i)
                port_dev[(se["dev"], se["port"])] = sd
                port_dev[(ae["dev"], ae["port"])] = ap_dev
                place(se["dev"])
                link_objs[lid] = {"devs": [sd, ap_dev], "errs": [], "ends": [se], "loss": 0.0,
                                  "chan": None, "wifi": True}
                sta_macs[se["dev"]] = (ns.DynamicCast[ns.WifiNetDevice](sd).GetMac(), lid, ap)
            emit(log=f"Wi-Fi {ap}: {cfg['standard']} {cfg['band']} GHz kanal {cfg['channel']} / {cfg['width']} MHz, "
                     f"{cfg['tx_power']:.0f} dBm, {len(members)} stansiya")
        mob = ns.MobilityHelper()
        mob.SetPositionAllocator(mob_pos)
        mob.SetMobilityModel("ns3::ConstantPositionMobilityModel")
        mob.Install(mob_nodes)

    emit(log=f"kabellar: {len(link_objs)}")
    bridge = ns.BridgeHelper()
    from lab.lag import groups as _lag_groups0
    if _st["bridges"] or _lag_groups0(project):
        # STP topologiya o'zgarishi (TCN) / bond vakil a'zosi almashishi FDB'ni tozalaydi; ns-3
        # bridge'da bunday mexanizm yo'q — MAC yozuvlari
        # qisqa yashaydi (aks holda 300 s eski port bo'yicha yuborilib qoladi)
        bridge.SetDeviceAttribute("ExpirationTime", ns.TimeValue(ns.Seconds(2)))
    for n, by_vlan in sw_ports.items():
        for _vl, cont in sorted(by_vlan.items()):
            if cont.GetN():
                bridge.Install(nodes[n], cont)

    # IP manzillar
    emit(log="IP manzillar")
    iface = {}        # (dev, port) -> (ipv4, idx, IPv4Interface)
    root_qd = {}      # (dev, port) -> QueueDisc
    for (dname, port), dv in port_dev.items():
        d = devs[dname]
        if d["type"] not in L3:
            continue
        ipv4 = family.protocol(nodes[dname])
        if (dname, port) in port_q:
            # Linux kabi: navbat (queue) IP qatlamidagi qdisc'da — pfifo limit=queue, qurilma
            # navbati kichik. Aks holda (qo'lda AddInterface) qdisc yo'q va TrafficControlLayer
            # to'lgan qurilma navbati oldida paketlarni hisobsiz tashlaydi.
            tch = ns.TrafficControlHelper()
            qkind = port_qd.get((dname, port), "fifo")
            if qkind == "prio":
                # qat'iy ustuvorlik: EF (TOS 0xb8 -> socket priority 4) -> band 0, qolganlari band 1
                tch.SetRootQueueDisc("ns3::PrioQueueDisc", "Priomap",
                                     ns.StringValue("1 1 1 1 0 1 1 1 1 1 1 1 1 1 1 1"))
            elif qkind == "fq_codel":
                tch.SetRootQueueDisc("ns3::FqCoDelQueueDisc", "MaxSize",
                                     ns.QueueSizeValue(ns.QueueSize(port_q[(dname, port)])))
            else:
                tch.SetRootQueueDisc("ns3::PfifoFastQueueDisc", "MaxSize",
                                     ns.QueueSizeValue(ns.QueueSize(port_q[(dname, port)])))
            qdc = tch.Install(dv)
            root_qd[(dname, port)] = qdc.Get(0)
            try:
                _dq = dv.GetObject[ns.NetDevice]()
                for cls in ("PointToPointNetDevice", "SimpleNetDevice"):
                    d_ = ns.DynamicCast[getattr(ns, cls)](_dq)
                    if d_:
                        d_.GetQueue().SetAttribute("MaxSize", ns.QueueSizeValue(ns.QueueSize("4p")))
                        break
            except Exception:  # noqa: BLE001
                pass
        ip = (((d.get("config") or {}).get("interfaces") or {}).get(f"{dname}-eth{port}") or {}).get("ip")
        idx, ii = family.bind(ipv4, dv, ip, d["type"] in ("router", "sdnrouter"))
        if ii:
            iface[(dname, port)] = (ipv4, idx, ii)
    # Hostlar tranzit bo'lmasin. Lekin ns-3 Ipv4GlobalRouting P2P bilan ulangan,
    # IpForward=false bo'lgan hostda segfault beradi (ns-3.44). Bitta interfeysli host
    # baribir tranzit bo'la olmaydi — faqat ko'p interfeyslilarda o'chiramiz.
    for name, d in devs.items():
        if version == 4 and d["type"] in ("host", "server") and sum(1 for (dn, _p) in iface if dn == name) > 1:
            nodes[name].GetObject[ns.Ipv4]().SetAttribute("IpForward", ns.BooleanValue(False))

    # statik marshrutlar + gateway (Ipv4StaticRouting ustuvorligi global'dan yuqori)
    srh = family.helper

    from lab.drivers.demo_driver import Net
    from lab.engines.ospf_convergence import OspfConvergence
    state = copy.deepcopy(project)      # joriy holat: kabel up/down, pozitsiyalar (routing uchun)
    ospf = OspfConvergence(sc)

    def install_routes():
        """Net.routes (ulangan + statik + gateway + dinamik[hop soni]) -> ns-3 statik marshrutlar.
        RIP routerlarda faqat foydalanuvchi statik marshrutlari (qolganini ns-3 RIP o'rganadi)."""
        n = ospf.apply(Net(state), ns.Simulator.Now().GetSeconds())
        for name, d in devs.items():
            if d["type"] not in L3:
                continue
            sr = family.routes(nodes[name])
            family.clear_gateway_routes(sr)
            for net, via, ifn, proto in n.routes(name):
                if via is None or (name in rip_nodes and proto != "static"):
                    continue
                k = (name, parse_ifname(ifn)[1])
                if k not in iface:
                    continue
                # A held route may still point at a failed interface. IPv6 SetDown
                # clears its addresses; installing that route makes ns-3 abort in
                # SourceAddressSelection. Leave it unusable until the link is up.
                ip, index, _ii = iface[k]
                if not ip.IsUp(index):
                    continue
                family.add_route(sr, net, via, index)

    emit(log=f"IPv{version} routing jadvallari (SPF, analitik bilan bir xil model)")
    emit(log=ospf.note())
    install_routes()
    if version == 6 and os.environ.get("NETLAB_NS3_DEBUG"):
        for name, node in nodes.items():
            if devs[name]["type"] in L3:
                ip = family.protocol(node)
                emit(log=f"{name}: forwarding={[ip.IsForwarding(i) for i in range(ip.GetNInterfaces())]} "
                         f"routes={sorted(family.route_keys(node, {}))}")
    emit(log="ilovalar")

    # ilovalar
    def ip_of(dname, explicit=None):
        if explicit:
            return explicit
        for (dn, _p), (_i, _x, ii) in sorted(iface.items()):
            if dn == dname:
                return str(ii.ip)
        return None

    fw_on = any((d.get("config") or {}).get("firewall") for d in devs.values())
    if fw_on:
        emit(log="eslatma: firewall ns-3 da paket darajasida yo'q — model bo'yicha bloklangan oqimlar ishga tushirilmaydi")

    fw_block = {}       # ilova -> (sabab, start, stop)

    def fw_blocked(app, i, dip):
        from lab.firewall import check_path, check_roundtrip, _why
        n_ = Net(state)
        sip_ = n_.ip_of(app["src"])
        ok_, _w, h_, _l, _d = n_.forward(app["src"], ipaddress.ip_address(dip))
        if not ok_ or not sip_:
            return None
        t_ = app.get("type", "udp")
        pkt = {"proto": "icmp" if t_ == "ping" else t_, "src": str(sip_.ip), "dst": dip, "sport": 40000,
               "dport": None if t_ == "ping" else (int(app["dport"]) if app.get("dport") not in (None, "") else 5201 + i)}
        if t_ == "udp":
            okf, v, w, c, r = check_path(n_, app["src"], dip, h_, pkt)
            return None if okf else _why(v, w, c, r, pkt)
        okf, why, _v = check_roundtrip(n_, app["src"], sip_.ip, app["dst"], dip, h_, pkt)
        return None if okf else why

    apps = []
    mc_groups = {}
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
        start = float(app.get("start", 1))
        # All engines clip finite windows to the scenario. Do this before
        # ns.Seconds(): a huge user stop must never overflow native time.
        stop = min(float(app.get("stop", duration)), duration)
        typ = app.get("type", "udp")
        if fw_on:
            # ns-3 da iptables yo'q: model (lab.firewall) bloklagan oqim ishga tushirilmaydi
            blocked = fw_blocked(app, i, dip)
            if blocked:
                emit(log=f"ilova {app.get('id')}: firewall bloklaydi ({blocked}) — ishga tushirilmaydi")
                fw_block[app.get("id") or f"a{i + 1}"] = (blocked, start, min(stop, duration))
                start = stop = duration + 1.0
        if app.get("mcast"):
            # multicast: guruh uchun BITTA OnOff (manba -> guruh manzili), har qabul qiluvchida sink
            g = app["mcast"]
            if g not in mc_groups:
                gi = len(mc_groups)
                gport = 9500 + gi
                oo = ns.OnOffHelper("ns3::UdpSocketFactory",
                                    ns.InetSocketAddress(ns.Ipv4Address(app["mgroup"]), gport).ConvertTo())
                oo.SetConstantRate(ns.DataRate(rate_str(app.get("rate", "1Mbps"))), int(app.get("size", 1000)))
                oo.SetAttribute("EnableSeqTsSizeHeader", ns.BooleanValue(True))
                gca = oo.Install(nodes[src])
                gca.Start(ns.Seconds(start)); gca.Stop(ns.Seconds(stop))
                nl.WatchMcTx(gca.Get(0), 100000 + gi)
                mc_groups[g] = {"port": gport, "gkey": 100000 + gi, "src": src, "group": app["mgroup"],
                                "rcv": [], "app": gca}
            mg = mc_groups[g]
            sink = ns.PacketSinkHelper("ns3::UdpSocketFactory",
                                       ns.InetSocketAddress(ns.Ipv4Address.GetAny(), mg["port"]).ConvertTo())
            sink.SetAttribute("EnableSeqTsSizeHeader", ns.BooleanValue(True))
            sa = sink.Install(nodes[dst]); sa.Start(ns.Seconds(0.0))
            nl.WatchMc(sa.Get(0), i)
            mg["rcv"].append((dst, dip))
            apps.append({"id": app.get("id") or f"a{i + 1}", "type": "udp", "src": src, "dst": dst, "port": mg["port"],
                         "label": app.get("label") or f"mcast {src}→{dst}", "start": start, "stop": min(stop, duration),
                         "mc_key": i, "mc_gkey": mg["gkey"], "mcast": g, "group": app.get("group", g),
                         "role": app.get("role")})
            continue
        from lab.model import dscp_class
        tos = 0xb8 if dscp_class(app.get("dscp")) == 0 else 0     # yuqori sinf -> EF
        addr = family.endpoint(dip, port)
        if typ == "ping":
            srv = ns.UdpEchoServerHelper(port)
            sa = srv.Install(nodes[dst]); sa.Start(ns.Seconds(0.0))
            cl = ns.UdpEchoClientHelper(family.address(dip).ConvertTo(), port)
            cl.SetAttribute("MaxPackets", ns.UintegerValue(10 ** 7))
            # No second echo inside the scenario needs an interval longer than
            # its entire duration; clip before scheduling to avoid time overflow.
            interval = min(float(app.get("interval", 0.2)), duration)
            cl.SetAttribute("Interval", ns.TimeValue(ns.Seconds(interval)))
            cl.SetAttribute("PacketSize", ns.UintegerValue(int(app.get("size", 64))))
            ca = cl.Install(nodes[src])
        else:
            proto = "ns3::TcpSocketFactory" if typ == "tcp" else "ns3::UdpSocketFactory"
            sink = ns.PacketSinkHelper(proto, family.any_endpoint(port))
            sa = sink.Install(nodes[dst]); sa.Start(ns.Seconds(0.0))
            if typ == "tcp" and not app.get("rate"):
                bs = ns.BulkSendHelper(proto, addr)
                bs.SetAttribute("MaxBytes", ns.UintegerValue(int(app.get("bytes") or 0)))   # 0 = cheksiz
                ca = bs.Install(nodes[src])
            else:
                oo = ns.OnOffHelper(proto, addr)
                oo.SetConstantRate(ns.DataRate(rate_str(app.get("rate", "1Mbps"))), int(app.get("size", 1000)))
                ca = oo.Install(nodes[src])
        ca.Start(ns.Seconds(start)); ca.Stop(ns.Seconds(stop))
        if tos and not ca.Get(0).SetAttributeFailSafe("Tos", ns.UintegerValue(tos)):
            emit(log=f"ilova {app.get('id')}: DSCP (Tos atributi) bu ns-3 ilovasida yo'q — oddiy sinf")
        if typ == "tcp" and app.get("bytes"):
            nl.WatchSink(sa.Get(0), i, int(app["bytes"]))
        apps.append({"id": app.get("id") or f"a{i + 1}", "type": typ, "src": src, "dst": dst, "port": port,
                     "label": app.get("label") or f"{typ} {src}→{dst}",
                     "start": start, "stop": min(stop, duration),
                     **({"bytes": int(app["bytes"]), "sink_key": i} if app.get("bytes") and typ == "tcp" else {}),
                     **({"group": app["group"], "role": app.get("role")} if app.get("group") else {})})

    # multicast marshrutlari: model daraxti (lab.mcast.branch) bo'yicha statik (PIM yo'q)
    mc_installed = []

    def install_mcast():
        if not mc_groups:
            return
        from lab.mcast import branch as mc_branch
        n_ = Net(state)
        for node_, origin, grp, iif in mc_installed:
            try:
                srh.GetStaticRouting(node_.GetObject[ns.Ipv4]()).RemoveMulticastRoute(origin, grp, iif)
            except Exception:  # noqa: BLE001
                pass
        mc_installed.clear()
        for g, mg in mc_groups.items():
            sip = ip_of(mg["src"])
            per_router = {}     # router -> [kirish port, {chiqish portlari}]
            src_eg = None
            for rcv, rip in mg["rcv"]:
                ok, _w, hops, _l, _d = n_.forward(mg["src"], ipaddress.ip_address(rip))
                if not ok:
                    continue
                okb, _wb, txp = mc_branch(n_, mg["src"], ipaddress.ip_address(rip), hops)
                if not okb or not txp:
                    continue
                src_eg = src_eg or txp[0][1]
                for (dv, eg), (_nd, in_ip) in zip(txp[1:], hops):
                    in_port = next((p_ for p_, f_, ipi in n_.ifaces(dv) if ipi and str(ipi.ip) == in_ip), None)
                    ent = per_router.setdefault(dv, [in_port, set()])
                    ent[1].add(parse_ifname(eg)[1])
            if src_eg and not mg.get("src_route"):
                ns.Ipv4StaticRoutingHelper().SetDefaultMulticastRoute(
                    nodes[mg["src"]], port_dev[(mg["src"], parse_ifname(src_eg)[1])])
                mg["src_route"] = True
            for dv, (in_port, outs) in per_router.items():
                if in_port is None or (dv, in_port) not in port_dev:
                    continue
                oc = ns.NetDeviceContainer()
                for o in sorted(outs):
                    if (dv, o) in port_dev:
                        oc.Add(port_dev[(dv, o)])
                srh.AddMulticastRoute(nodes[dv], ns.Ipv4Address(sip), ns.Ipv4Address(mg["group"]),
                                      port_dev[(dv, in_port)], oc)
                ipv4_ = nodes[dv].GetObject[ns.Ipv4]()
                mc_installed.append((nodes[dv], ns.Ipv4Address(sip), ns.Ipv4Address(mg["group"]),
                                     ipv4_.GetInterfaceForDevice(port_dev[(dv, in_port)])))
        emit(log=f"multicast: {len(mc_groups)} guruh, {len(mc_installed)} router'da statik mroute (PIM ekvivalenti)")

    install_mcast()
    fmh = ns.FlowMonitorHelper()
    if os.environ.get("NETLAB_NS3_FM", "endpoints") == "all":
        mon = fmh.InstallAll()
    else:
        # FlowMonitor faqat ilovalar uchlarida: oqim statistikasi (tx manbada, rx/kechikish/jitter
        # qabul qiluvchida) uchlarda yoziladi — har hop'dagi probe'lar (paket tegi, hash) behuda.
        fm_nodes = ns.NodeContainer()
        for n_ in sorted({x for a_ in apps for x in (a_["src"], a_["dst"])}, key=lambda n_: devs[n_]["id"]):
            if devs[n_]["type"] in L3:
                fm_nodes.Add(nodes[n_])
        mon = fmh.Install(fm_nodes)
    clf = family.classifier(fmh)

    # hodisalar
    def set_link(lid, up=None, params=None):
        lo = link_objs.get(lid)
        if not lo:
            return
        if params and not lo.get("wifi"):       # Wi-Fi: tezlik radio modelidan, o'zgarmaydi
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
                    _set_err(em, lo["loss"])
        if up is not None:
            if (state["links"][lid].get("up") is not False) != bool(up):
                ospf.before_change(state, ns.Simulator.Now().GetSeconds())
            for em in lo["errs"]:
                _set_err(em, lo["loss"] if up else 1.0)
            for end in lo["ends"]:
                k = (end["dev"], end["port"])
                if k in iface:
                    ipv4, idx, ii = iface[k]
                    family.set_link_state(ipv4, idx, up, ii)
            if lid in state["links"]:
                state["links"][lid]["up"] = bool(up)
            install_routes()
            install_mcast()

    # STP: model daraxti; blocking (va konvergensiya kutayotgan) segmentlar ma'lumot uchun yopiq
    from lab.stp import compute as stp_compute2, convergence_time as stp_conv_time, stp_enabled
    stp_on = any(stp_enabled(d) for d in devs.values())
    stp_s = {"up": {}, "fwd": None, "hold": {}}

    def stp_tick(t):
        if not stp_on:
            return
        up_now = {lid: l.get("up") is not False for lid, l in state["links"].items()}
        failed = [lid for lid, u in up_now.items() if stp_s["up"].get(lid, True) and not u]
        changed = up_now != stp_s["up"]
        stp_s["up"] = up_now
        if changed or stp_s["fwd"] is None:
            st_new = stp_compute2(state)
            fwd_new = {(x, p_) for x, b in st_new["bridges"].items() for p_, v in b["ports"].items()
                       if v["state"] == "forwarding"}
            if stp_s["fwd"] is not None:
                for x, p_ in fwd_new - stp_s["fwd"]:
                    stp_s["hold"][(x, p_)] = t + stp_conv_time(state, x, failed, sc)
                    emit(log=f"STP: {x} port {p_} -> forwarding {stp_s['hold'][(x, p_)] - t:.0f} s dan keyin")
                for key in list(stp_s["hold"]):
                    if key not in fwd_new:
                        stp_s["hold"].pop(key)
            stp_s["fwd"] = fwd_new
        for key in [k_ for k_, t_ in stp_s["hold"].items() if t + 1e-9 >= t_]:
            stp_s["hold"].pop(key)
        st = stp_compute2(state, sorted(stp_s["hold"]))
        for lid, lo in link_objs.items():
            ends = lo["ends"]
            if not all(stp_enabled(devs[e_["dev"]]) for e_ in ends):
                continue
            up = state["links"][lid].get("up") is not False
            blk = any((e_["dev"], e_["port"]) in st["blocked"] for e_ in ends)
            for em in lo["errs"]:
                _set_err(em, 1.0 if (not up or blk) else lo["loss"])
        for x, b in sorted(st["bridges"].items()):     # tartib deterministik (set iteratsiyasi hash'ga bog'liq)
            rtrack.update(t, x, {f"port{p_} {v['role']}/{v['state']}" for p_, v in b["ports"].items()})

    # Link aggregation: ns-3 da bonding yo'q — bond BITTA "vakil" a'zo kabel orqali yuradi,
    # uning DataRate'i = ishlaydigan a'zolar yig'indisi; qolgan a'zolar yopiq (ErrorModel=1,
    # aks holda bridge'lar orasida L2 halqa). Oqim bo'yicha hash modellashtirilmaydi; kabel
    # statistikasi vakil a'zoda (yig'indi). STP tick'idan keyin qo'llanadi.
    from lab.lag import groups as lag_groups
    lag_g = lag_groups(state)
    if lag_g:
        emit(log="bond (LAG): ns-3 da bitta vakil kabel, tezlik = a'zolar yig'indisi: "
                 + ", ".join(f"{g['a'][0]}/{g['a'][1]}<->{g['b'][0]}/{g['b'][1]} [{', '.join(g['links'])}]" for g in lag_g))

    def lag_tick(_t):
        for g in lag_g:
            up = [lid for lid in g["links"] if state["links"][lid].get("up") is not False and lid in link_objs]
            total = sum(float((state["links"][lid].get("params") or {}).get("bw", 100)) for lid in up)
            for lid in g["links"]:
                lo = link_objs.get(lid)
                if not lo:
                    continue
                if up and lid == up[0]:
                    r = ns.StringValue(rate_str(total))
                    for dv in lo["devs"]:
                        dv.SetAttributeFailSafe("DataRate", r)
                    for em in lo["errs"]:
                        _set_err(em, lo["loss"])
                else:
                    for em in lo["errs"]:
                        _set_err(em, 1.0)

    events = sorted(sc.get("events") or [], key=lambda e: float(e.get("t", 0)))
    initially_down = [lid for lid, l in project["links"].items() if l.get("up") is False]
    for lid in initially_down:
        set_link(lid, up=False)

    # qadamli ijro: har 1s (va har hodisa vaqtida) to'xtab, FlowMonitor o'lchanadi
    step = float(sc.get("sample", 1.0))
    sampling_marks = {k * step for k in range(1, int(math.ceil(duration / step - 1e-8)))} | {duration}
    marks = sorted(sampling_marks
                   | {float(e["t"]) for e in events if 0 < float(e.get("t", 0)) < duration}
                   | ({round(float(e["t"]) + dd, 6) for e in events for dd in (1.0, 30.0, 50.0)
                       if 0 < float(e.get("t", 0)) + dd < duration} if stp_on else set()))
    if ospf.delay_s:
        marks = sorted(set(marks) | {float(e.get("t", 0)) + ospf.delay_s for e in events
                       if e.get("action") in ("link_down", "link_up")
                       and 0 < float(e.get("t", 0)) + ospf.delay_s < duration})
    # harakat: pozitsiyalar har 0.5s da yangilanadi (oraliq nuqtalarda namuna olinmaydi)
    from lab.mobility import has_moves, positions_at
    movers = has_moves(sc)
    if movers:
        marks = sorted(set(marks) | {round(k * 0.5, 6) for k in range(1, int(duration * 2))})
        mpp_m = __import__("lab.wifi", fromlist=["meters_per_px"]).meters_per_px(project)

    from lab.mobility import apply_positions
    from lab.wifi import link_estimate as _wle
    wifi_ok_sig = [None]

    def update_positions(t):
        pos = positions_at(project, sc, t)
        apply_positions(state, pos)
        sig = tuple(sorted(lid for lid, l in state["links"].items()
                           if (l.get("params") or {}).get("medium") == "wifi" and (_wle(state, lid) or {}).get("ok")))
        if wifi_ok_sig[0] is not None and sig != wifi_ok_sig[0]:
            install_routes()          # stansiya qamrovdan chiqdi/kirdi — yo'llar qayta hisoblanadi
        wifi_ok_sig[0] = sig
        for dev, (x, y) in pos.items():
            if dev in placed:     # faqat Wi-Fi ishtirokchilarida mobility bor
                nodes[dev].GetObject[ns.MobilityModel]().SetPosition(ns.Vector(x * mpp_m, y * mpp_m, 1.5))
    prev = {}
    series = {a["id"]: [] for a in apps}
    now = 0.0
    last_sample = 0.0       # oxirgi namuna vaqti (harakat bo'lsa qadam 0.5s, namuna esa 1s)
    roam_state = {}         # stansiya -> joriy AP
    ev_i = 0
    applied = []

    # kabel statistikasi: har yo'nalish = jo'natuvchi qurilmaning TX navbati (DropTail)
    def _txq(dv):
        for cls in ("PointToPointNetDevice", "SimpleNetDevice"):
            try:
                d = ns.DynamicCast[getattr(ns, cls)](dv)
                if d:
                    return d.GetQueue()
            except Exception:  # noqa: BLE001
                pass
        return None
    def _qd_stats(qd):
        if not qd:
            return 0, 0
        try:
            return qd.GetNPackets(), qd.GetStats().nTotalDroppedPackets
        except Exception as ex:  # noqa: BLE001
            if os.environ.get("NETLAB_NS3_DEBUG"):
                emit(log=f"qdisc stats: {ex}")
            return 0, 0
    link_q = {lid: [_txq(dv) for dv in lo["devs"]] for lid, lo in link_objs.items()}
    link_qd = {lid: [root_qd.get((e["dev"], e["port"])) for e in lo["ends"]] for lid, lo in link_objs.items()}
    link_series = {lid: [] for lid in link_q}
    link_prev = {}

    def sample_links(dt):
        for lid, (qa, qb) in link_q.items():
            if qa is None or qb is None:
                continue
            (na, da), (nb, db) = (_qd_stats(x) for x in link_qd[lid])
            cur = (qa.GetTotalReceivedBytes() - qa.GetTotalDroppedBytes(), qa.GetTotalDroppedPackets() + da,
                   qb.GetTotalReceivedBytes() - qb.GetTotalDroppedBytes(), qb.GetTotalDroppedPackets() + db)
            p0 = link_prev.get(lid, (0, 0, 0, 0))
            link_prev[lid] = cur
            ab = (cur[0] - p0[0]) * 8 / max(dt, 1e-9)
            ba = (cur[2] - p0[2]) * 8 / max(dt, 1e-9)
            bw = float((project["links"][lid].get("params") or {}).get("bw", 100)) * 1e6
            link_series[lid].append({"t": round(now, 3), "ab_bps": round(ab), "ba_bps": round(ba),
                                     "util": round(min(max(ab, ba) / bw, 1.0), 4),
                                     "queue_ab": qa.GetNPackets() + na, "queue_ba": qb.GetNPackets() + nb,
                                     "drops_ab": cur[1] - p0[1], "drops_ba": cur[3] - p0[3]})

    idx_ifn = {}
    for (dn, port), (_ipv4, idx, _ii) in iface.items():
        idx_ifn.setdefault(dn, {})[idx] = f"{dn}-eth{port}"
    node_series = {n: [] for n in stat_nodes}
    rtrack = RouteTracker()
    node_prev = {}

    def sample_nodes(dt):
        for n in stat_nodes:
            rts = (family.route_keys(nodes[n], idx_ifn.get(n, {})) if version == 6 else
                   parse_ns3_routes(str(nl.Routes(nodes[n])), idx_ifn.get(n, {})))
            rtrack.update(now, n, rts)
            cur = [int(x) for x in nl.Get(nodes[n].GetId())]
            p0 = node_prev.get(n, cur)
            node_prev[n] = cur
            d = [c - p for c, p in zip(cur, p0)]
            disc = sum(link_series[lid][-1][f"drops_{dd}"] for lid, lo in link_objs.items() if link_series.get(lid)
                       for side, dd in ((0, "ab"), (1, "ba")) if lo["ends"][side]["dev"] == n)
            node_series[n].append({"t": round(now, 3), "routes": len(rts),
                                   "fwd_pps": round(d[0] / max(dt, 1e-9), 1), "rx_pps": round(d[1] / max(dt, 1e-9), 1),
                                   "tx_pps": round(d[2] / max(dt, 1e-9), 1), "noroute": d[3], "discards": d[4] + disc})

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

    for n in stat_nodes:        # t=0 holat
        rtrack.update(0, n, (family.route_keys(nodes[n], idx_ifn.get(n, {})) if version == 6 else
                   parse_ns3_routes(str(nl.Routes(nodes[n])), idx_ifn.get(n, {}))))
        node_prev[n] = [int(x) for x in nl.Get(nodes[n].GetId())]
    # VRRP: ns-3 da yo'q — model vaqt jadvali (lab/vrrp.timeline: Master_Down_Interval, preempt)
    # bo'yicha VIP joriy master interfeysiga qo'shimcha manzil sifatida qo'yiladi/olinadi va
    # ARP keshlari tozalanadi (gratuitous ARP ekvivalenti).
    from lab import vrrp as _vrrp
    vrrp_tl = _vrrp.timeline(project, sc)
    vrrp_hold = {}       # group_key -> (router, port) VIP hozir qayerda
    vrrp_ent = {key: {r: e for r, e in mem} for key, mem in _vrrp.groups(project).items()}

    def vrrp_tick(t):
        if not vrrp_tl:
            return
        want = _vrrp.masters_at(vrrp_tl, t)
        changed = False
        for key, r in want.items():
            cur = vrrp_hold.get(key)
            if (cur[0] if cur else None) == r:
                continue
            vip = ipaddress.ip_interface(next(iter(vrrp_ent[key].values()))["vip"])
            if cur and cur in iface:
                ipv4, idx, _ii = iface[cur]
                for ai in range(ipv4.GetNAddresses(idx) - 1, -1, -1):
                    if str(ipv4.GetAddress(idx, ai).GetLocal()) == str(vip.ip):
                        ipv4.RemoveAddress(idx, ai)
            vrrp_hold.pop(key, None)
            if r:
                port = parse_ifname(vrrp_ent[key][r]["iface"])[1]
                if (r, port) in iface:
                    ipv4, idx, _ii = iface[(r, port)]
                    ipv4.AddAddress(idx, ns.Ipv4InterfaceAddress(ns.Ipv4Address(str(vip.ip)), ns.Ipv4Mask(str(vip.netmask))))
                    vrrp_hold[key] = (r, port)
            changed = True
            if t > 0:
                emit(log=f"t={t:.1f}s: VRRP {key} master -> {r or 'yoq'}")
        if changed:
            state["_vrrp_master"] = want
            for nn, dd in devs.items():
                if dd["type"] not in L3:
                    continue
                l3p = nodes[nn].GetObject[ns.Ipv4L3Protocol]()
                for ii_ in range(l3p.GetNInterfaces()):
                    c_ = l3p.GetInterface(ii_).GetArpCache()
                    if c_:
                        c_.Flush()
    if vrrp_tl:
        emit(log="eslatma: VRRP ns-3 da yo'q — master model vaqt jadvali bo'yicha (VIP qo'shimcha manzil sifatida ko'chiriladi)")
        marks = sorted(set(marks) | {round(tt, 6) for tt, _k, _r in vrrp_tl if 0 < tt < duration})
        vrrp_tick(0.0)

    sample_intervals = []
    for mark in marks:
        while ev_i < len(events) and float(events[ev_i].get("t", 0)) <= now + 1e-9:
            e = events[ev_i]; ev_i += 1
            act = e.get("action")
            if act != "move":
                set_link(e.get("link"), up=(act == "link_up") if act in ("link_up", "link_down") else None,
                         params=e.get("params") if act == "link_params" else None)
            applied.append({"t": now, **e})
            emit(log=f"t={now:.1f}s: {act} {e.get('link') or e.get('device')}")
        if ospf.due(now):
            install_routes()
        stp_tick(now)
        vrrp_tick(now)
        lag_tick(now)
        if movers:
            update_positions(now)
        ns.Simulator.Stop(ns.Seconds(mark - now))
        ns.Simulator.Run()
        now = mark
        if mark not in sampling_marks:
            continue   # oraliq nuqta (hodisa/harakat) — namuna olinmaydi
        dt = now - last_sample
        sample_start = last_sample
        last_sample = now
        sample_intervals.append((sample_start, now))
        # roaming: bir xil SSID'li (ESS) AP'lar orasida ns-3 o'zi qayta ulanadi — BSSID'ni kuzatamiz
        for sta, (smac, _lid, ap0) in sta_macs.items():
            cur_ap = None
            if ns.DynamicCast[ns.StaWifiMac](smac).IsAssociated():
                b = smac.GetBssid(0)
                cur_ap = next((n_ for m_, n_ in ap_macs if m_ == b), None)
            if not cur_ap:
                continue                      # hozir ulanmagan (handover jarayonida)
            prev_ap = roam_state.get(sta, ap0)   # boshlang'ich — kabel ulangan AP
            if cur_ap != prev_ap:
                applied.append({"t": now, "action": "roam", "device": sta, "from": prev_ap, "to": cur_ap})
                emit(log=f"t={now:.0f}s: roaming {sta}: {prev_ap} -> {cur_ap}")
            roam_state[sta] = cur_ap
        mon.CheckForLostPackets()
        for app in apps:
            if "mc_key" in app:         # multicast qabul qiluvchi: C++ sink hisoblagichlari
                cur = [float(x) for x in nl.McGet(app["mc_key"], app["mc_gkey"])]
                p0 = prev.get(app["id"], [0.0] * 5)
                prev[app["id"]] = cur
                d_ = [c - q for c, q in zip(cur, p0)]
                series[app["id"]].append({
                    "t": round(now, 3), "t_start": sample_start, "t_end": now,
                    "thr_bps": round(d_[0] * 8 / max(dt, 1e-9)),
                    "delay_ms": round(d_[2] / d_[1] * 1000, 3) if d_[1] > 0 else None,
                    "jitter_ms": round(d_[3] / d_[1] * 1000, 3) if d_[1] > 0 else None,
                    "loss_pct": round(max(d_[4] - d_[1], 0) / d_[4] * 100, 2) if d_[4] > 0 else None})
                continue
            fwd, rev = flows_for(app)
            cur = (agg(fwd), agg(rev))
            p0 = prev.get(app["id"], ((0, 0, 0, 0.0, 0.0), (0, 0, 0, 0.0, 0.0)))
            prev[app["id"]] = cur
            (tx, rx, rxb, dl, jt), (rtx, rrx, _rb, rdl, _rj) = cur
            (tx0, rx0, rxb0, dl0, jt0), (rtx0, rrx0, _rb0, rdl0, _rj0) = p0
            drx, dtx = rx - rx0, tx - tx0
            pt = {"t": round(now, 3), "t_start": sample_start, "t_end": now,
                  "thr_bps": round((rxb - rxb0) * 8 / max(dt, 1e-9)),
                  "delay_ms": round((dl - dl0) / drx * 1000, 3) if drx > 0 else None,
                  "jitter_ms": round((jt - jt0) / (drx - 1) * 1000, 3) if drx > 1 else None,
                  "loss_pct": round(max(dtx - drx, 0) / dtx * 100, 2) if dtx > 0 else None}
            if app["type"] == "ping":
                drr = rrx - rrx0
                pt["rtt_ms"] = (round(pt["delay_ms"] + (rdl - rdl0) / drr * 1000, 3)
                                if pt["delay_ms"] is not None and drr > 0 else None)
            series[app["id"]].append(pt)
        sample_links(dt)
        sample_nodes(dt)
        emit(progress=round(now / duration, 3))

    if os.environ.get("NETLAB_NS3_DEBUG"):
        for fid, st_ in mon.GetFlowStats():
            t5 = clf.FindFlow(fid)
            emit(log=f"flow {fid}: {t5.sourceAddress}:{t5.sourcePort} -> {t5.destinationAddress}:{t5.destinationPort} "
                     f"proto {t5.protocol} tx {st_.txPackets} rx {st_.rxPackets} rxB {st_.rxBytes}")
    # yakuniy xulosa
    flows = {}
    for app in apps:
        if "mc_key" in app:
            b_, pk_, ds_, js_, tx_ = [float(x) for x in nl.McGet(app["mc_key"], app["mc_gkey"])]
            active = max(app["stop"] - app["start"], 1e-9)
            flows[app["id"]] = {**{k: v for k, v in app.items() if k not in ("mc_key", "mc_gkey")},
                                "series": series[app["id"]],
                                "summary": {"tx_pkts": int(tx_), "rx_pkts": int(pk_), "rx_bytes": int(b_),
                                            "avg_thr_bps": round(b_ * 8 / active),
                                            "avg_delay_ms": round(ds_ / pk_ * 1000, 3) if pk_ else None,
                                            "avg_jitter_ms": round(js_ / pk_ * 1000, 3) if pk_ else None,
                                            "loss_pct": round(max(tx_ - pk_, 0) / tx_ * 100, 2) if tx_ else None}}
            continue
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
        flows[app["id"]] = {**{k: v for k, v in app.items() if k != "sink_key"},
                            "series": series[app["id"]], "summary": summ}
        if app["id"] in fw_block:
            why_, st_, sp_ = fw_block[app["id"]]
            flows[app["id"]].update(
                start=st_, stop=sp_, blocked=why_,
                series=[{"t": round(b, 3), "t_start": a, "t_end": b, "thr_bps": 0,
                         "delay_ms": None, "jitter_ms": None, "loss_pct": 100.0}
                        for a, b in sample_intervals if st_ <= a < sp_],
                summary={"avg_thr_bps": 0, "avg_delay_ms": None, "avg_jitter_ms": None, "loss_pct": 100.0,
                         "blocked": why_})
        if "sink_key" in app:
            dt_ = float(nl.DoneAt(app["sink_key"]))
            flows[app["id"]]["done_t"] = round(dt_, 4) if dt_ >= 0 else None
    ns.Simulator.Destroy()
    out = {"engine": "ns3", "engine_label": "ns-3 (diskret-hodisali simulyatsiya)",
           "ip_version": version, "duration": duration, "flows": flows, "links": link_series, "events": applied,
           "nodes": node_series, "route_events": rtrack.events,
           "convergence": convergence(applied, rtrack.events, duration),
           "wall_s": round(time.time() - t_wall, 2),
           "notes": ["dinamik routing: ripd → ns-3 RIPv2 (haqiqiy konvergensiya); qolganlari — analitik bilan bir xil SPF modeli statik marshrut sifatida",
                     ospf.note(),
                     f"TCP: Cubic, MSS {mtu - 52}, bufer 4/6 MB (Linux'ga yaqin)",
                     "Wi-Fi: YansWifi + LogDistance(n=3), IdealWifiManager; joylashuv = kanvas * meters_per_px",
                     "jitter parametri modellashtirilmaydi", "P4/SDN mantiqi: switch L2 bridge, SDN router — L3 router sifatida"]}
    if fast:
        out["ns3_fast"] = True
        out["notes"].append("ns3_fast: jumbo MTU 9000 (TCP paketlari ~6x kam) — taxminiy rejim; "
                            "paket darajasidagi kechikish/jitter aniq rejimdan farq qilishi mumkin")
    with open(out_path, "w") as f:
        json.dump(out, f)
    emit(progress=1.0, log=f"tugadi ({out['wall_s']}s)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
