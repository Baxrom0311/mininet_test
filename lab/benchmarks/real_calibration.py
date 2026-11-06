"""Real Mininet/FRR/OVS'da protokol konvergensiyasini o'lchab, `lab.calibration` CSV'sini yozadi.

    sudo python3 -m lab.benchmarks.real_calibration --only ospf ospf-up rip --reps 10 --output /tmp/measurements.csv
    python3 -m lab.calibration /tmp/measurements.csv --format markdown

Har takror alohida (toza) tarmoqda bajariladi. Ikki xil metrika yoziladi va ular
aralashtirilmaydi:
  * protokolning o'z holati (`port_forwarding_s`, `master_down_s`, `route_recovery_s`,
    `session_recovery_s`, `route_update_s`) — hodisadan keyin qurilmadagi holat
    (OVS port holati, `show vrrp`, kernel marshruti) kutilgan qiymatga o'tguncha;
  * `traffic_gap_s` — hostdan 0.1 s oraliqli ping'dagi eng uzun javobsiz oraliq
    (protokol + ARP/qaytish yo'li + probe intervali).
`model` ustuni — model funksiyalari (lab.stp / lab.vrrp / analitik standartlari) qiymati.
Real tarmoq testlari bilan bir vaqtda ishlamasligi uchun /tmp/netlab-real.lock olinadi.
Every attempt is recorded in OUTPUT.collection.json. Partial collection exits 1;
OSPF up/down metrics are separate. See docs/calibration-collector.md.
"""

import argparse
import ipaddress
import os
from pathlib import Path
import platform
import re
import socket
import sys
import time

from lab.real_lock import serial_real_test
from lab.benchmarks.calibration_probes import Pinger, poll
from lab.benchmarks.calibration_collection import FIELDS, collect
from lab.drivers.base import EventBus
from lab.drivers.mininet_driver import MininetDriver
from lab.tests.real_integration import load, wait_ping

def _ip(project, dev):
    ic = next(iter(project["devices"][dev]["config"]["interfaces"].values()))
    return str(ipaddress.ip_interface(ic["ip"]).ip)


def _ovs_state(drv, sw, port, mode):
    out = drv.net.get(sw).cmd(f"ovs-appctl {'rstp/show' if mode == 'rstp' else 'stp/show'} {sw}")
    for line in out.splitlines():
        m = re.match(rf"\s*{sw}-eth{port}\s+(\w+)\s+(\w+)", line)
        if m:
            a, b = m.group(1).lower(), m.group(2).lower()
            return a if a in ("forwarding", "blocking", "discarding", "learning", "listening", "disabled") else b
    return None


def m_stp(drv, mode):
    from lab.stp import convergence_time
    p = load("stp_lab")
    for n in ("s1", "s2", "s3"):
        p["devices"][n]["config"]["stp_mode"] = mode
    drv.start(p)
    h2 = _ip(p, "h2")
    if wait_ping(drv, "h1", h2, 120) is None or poll(lambda: _ovs_state(drv, "s3", 2, mode) in ("blocking", "discarding"), 60) is None:
        raise RuntimeError(f"{mode}: boshlang'ich holat tayyor emas")
    with Pinger(drv, "h1", h2) as pg:
        pg.wait_ready()
        time.sleep(2)
        t_ev, t_mono = pg.mark_event()
        drv.update_link("l2", up=False)
        fwd = poll(lambda: _ovs_state(drv, "s3", 2, mode) == "forwarding", 90, started_at=t_mono)
        time.sleep(8 if mode == "stp" else 4)
    if not pg.ready_before(t_ev):
        raise RuntimeError("hodisagacha ping javobi yo'q — takror yaroqsiz")
    model = convergence_time(drv.project, "s3", ["l2"])
    return [(mode, "port_forwarding_s", model, fwd, 3.0, 0.2), (mode, "traffic_gap_s", model, pg.gap_after(t_ev), 3.0, 0.3)]


def _vrrp_state(drv, r):
    out = drv.net.get(r).cmd(f"vtysh -N {r} -c 'show vrrp' 2>&1")
    m = re.search(r"Status \(v4\)\s+(\w+)", out)
    return m.group(1) if m else None


def m_vrrp(drv):
    from lab.vrrp import mdi
    p = load("vrrp_lab")
    drv.start(p)
    srv = _ip(p, "srv1")
    if poll(lambda: _vrrp_state(drv, "r1") == "Master", 30) is None or wait_ping(drv, "pc1", srv, 90) is None:
        raise RuntimeError("vrrp: boshlang'ich holat tayyor emas")
    with Pinger(drv, "pc1", srv) as pg:
        pg.wait_ready()
        time.sleep(2)
        t_ev, t_mono = pg.mark_event()
        drv.update_link("l3", up=False)
        md = poll(lambda: _vrrp_state(drv, "r2") == "Master", 20, step=0.05, started_at=t_mono)
        time.sleep(12)
    if not pg.ready_before(t_ev):
        raise RuntimeError("hodisagacha ping javobi yo'q — takror yaroqsiz")
    e = p["devices"]["r2"]["config"]["vrrp"][0]
    model = mdi(e)
    return [("vrrp", "master_down_s", model, md, 1.0, 0.2), ("vrrp", "traffic_gap_s", model, pg.gap_after(t_ev), 3.0, 0.5)]


def _route_via(drv, r, dst):
    out = drv.net.get(r).cmd(f"ip -color=never -4 route get {dst} 2>/dev/null")
    m = re.search(r"via (\S+)", out)
    return m.group(1) if m else None


def m_rip(drv, update=10):
    p = load("rip_ring")
    for n in ("r1", "r2", "r3"):
        p["devices"][n]["config"]["rip_timers"] = {"update": update, "timeout": update * 6, "garbage": update * 4}
    drv.start(p)
    srv = _ip(p, "srv1")
    # ping birinchi o'tganda RIP hali yaqinlashmagan bo'lishi mumkin (r1 yo'lni vaqtincha r3 orqali
    # oladi) — o'lchov uchun barqaror holat: srv1 LAN r2 (10.12.0.2) orqali
    if wait_ping(drv, "pc1", srv, 120) is None or poll(lambda: _route_via(drv, "r1", srv) == "10.12.0.2", 90) is None:
        raise RuntimeError("rip: boshlang'ich holat tayyor emas")
    before = "10.12.0.2"
    with Pinger(drv, "pc1", srv) as pg:
        pg.wait_ready()
        time.sleep(2)
        if _route_via(drv, "r1", srv) != before:
            raise RuntimeError("rip: boshlang'ich marshrut hodisagacha o'zgardi")
        t_ev, t_mono = pg.mark_event()
        drv.update_link("l2", up=False)
        rec = poll(lambda: _route_via(drv, "r1", srv) not in (None, before), update * 4 + 10, started_at=t_mono)   # yangi qo'shni orqali
        time.sleep(3)
    if not pg.ready_before(t_ev):
        raise RuntimeError("hodisagacha ping javobi yo'q — takror yaroqsiz")
    # analitik standart: muqobil yo'l keyingi periodik update'da — o'rtacha update/2
    return [("rip", "route_recovery_s", update / 2, rec, update / 2 + 1, 0.0),
            ("rip", "traffic_gap_s", update / 2, pg.gap_after(t_ev), update / 2 + 2, 0.0)]


def m_bgp(drv):
    p = load("bgp_lab")
    drv.start(p)
    srv = _ip(p, "srv1")
    if wait_ping(drv, "pc1", srv, 120) is None:
        raise RuntimeError("bgp: boshlang'ich holat tayyor emas")
    has = lambda: bool(drv.net.get("r3").cmd("ip -color=never -4 route show 10.1.0.0/24").strip())  # noqa: E731
    drv.update_link("l4", up=False)
    if poll(lambda: not has(), 20) is None:
        raise RuntimeError("bgp: marshrut olib tashlanmadi")
    time.sleep(2)
    with Pinger(drv, "pc1", srv) as pg:
        t_ev, t_mono = pg.mark_event(require_ready=False)
        drv.update_link("l4", up=True)
        rec = poll(has, 60, started_at=t_mono)
        time.sleep(3)
    # link up -> ping qaytguncha (bu yerda gap = hodisadan birinchi javobgacha)
    first = pg.first_after(t_ev)
    return [("bgp", "session_recovery_s", 2.0, rec, 2.0, 0.5),
            ("bgp", "traffic_recovery_s", 2.0, first, 3.0, 0.5)]


def m_ospf(drv, restore=False):
    from lab.topogen import generate
    p = generate("ring", routers=4, hosts=1, flows=0)
    drv.start(p)
    dst = _ip(p, "h1x0")
    link = p["links"]["l1"]
    end = next(link[side] for side in ("a", "b") if link[side]["dev"] == "r1")
    expected = str(ipaddress.ip_interface(p["devices"]["r1"]["config"]["interfaces"][f"r1-eth{end['port']}"]["ip"]).ip)
    if (wait_ping(drv, "h0x0", dst, 120) is None
            or poll(lambda: _route_via(drv, "r0", dst) == expected, 30, step=.05) is None):
        raise RuntimeError("ospf: boshlang'ich holat tayyor emas")
    before = expected
    if restore:
        drv.update_link("l1", up=False)
        if (poll(lambda: _route_via(drv, "r0", dst) not in (None, before), 30, step=.05) is None
                or wait_ping(drv, "h0x0", dst, 30) is None):
            raise RuntimeError("ospf-up: muqobil yo'l tayyor emas")
    with Pinger(drv, "h0x0", dst) as pg:
        pg.wait_ready()
        time.sleep(2)
        current = _route_via(drv, "r0", dst)
        if current is None or ((current == before) == restore):
            raise RuntimeError("ospf: boshlang'ich marshrut hodisagacha o'zgardi")
        t_ev, t_mono = pg.mark_event()
        drv.update_link("l1", up=restore)        # r0-r1
        want = (lambda: _route_via(drv, "r0", dst) == before) if restore else (
            lambda: _route_via(drv, "r0", dst) not in (None, before))
        upd = poll(want, 30, step=.05, started_at=t_mono)
        time.sleep(4)
    if not pg.ready_before(t_ev):
        raise RuntimeError("hodisagacha ping javobi yo'q — takror yaroqsiz")
    # analitik model: SPF darhol (0 s); hello 1 s / dead 4 s, interfeys o'chishi — darhol aniqlanadi
    return [("ospf", "route_restore_s" if restore else "route_update_s", 0.0, upd, 1.0, 0.0),
            ("ospf", "traffic_gap_up_s" if restore else "traffic_gap_s", 0.0, pg.gap_after(t_ev), 1.5, 0.0)]


MEASURES = {"stp": lambda d: m_stp(d, "stp"), "rstp": lambda d: m_stp(d, "rstp"), "vrrp": m_vrrp,
            "rip": m_rip, "bgp": m_bgp, "ospf": m_ospf, "ospf-up": lambda d: m_ospf(d, restore=True)}

EXPECTED = {"stp": [("stp", "port_forwarding_s"), ("stp", "traffic_gap_s")],
            "rstp": [("rstp", "port_forwarding_s"), ("rstp", "traffic_gap_s")],
            "vrrp": [("vrrp", "master_down_s"), ("vrrp", "traffic_gap_s")],
            "rip": [("rip", "route_recovery_s"), ("rip", "traffic_gap_s")],
            "bgp": [("bgp", "session_recovery_s"), ("bgp", "traffic_recovery_s")],
            "ospf": [("ospf", "route_update_s"), ("ospf", "traffic_gap_s")],
            "ospf-up": [("ospf", "route_restore_s"), ("ospf", "traffic_gap_up_s")]}


@serial_real_test
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", nargs="+", choices=sorted(MEASURES), default=sorted(MEASURES))
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--report", type=Path, help="attempt ledger (default: OUTPUT.collection.json)")
    a = ap.parse_args(argv)
    if not 1 <= a.reps <= 1000:
        ap.error("reps must be 1..1000")
    if len(set(a.only)) != len(a.only):
        ap.error("--only cases must not repeat")
    report_path = a.report or Path(str(a.output) + ".collection.json")
    if report_path.resolve() == a.output.resolve():
        ap.error("CSV and report paths must differ")
    if os.geteuid() != 0:
        print("root kerak")
        return 2
    host = f"{socket.gethostname()}/{platform.release()}"
    drv = MininetDriver(EventBus())
    report = collect(drv, MEASURES, EXPECTED, a.only, a.reps, a.output, report_path, host,
                     log=lambda message: print(message, flush=True))
    return int(report["status"] != "passed")


if __name__ == "__main__":
    sys.exit(main())
