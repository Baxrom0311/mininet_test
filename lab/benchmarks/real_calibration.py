"""Real Mininet/FRR/OVS'da protokol konvergensiyasini o'lchab, `lab.calibration` CSV'sini yozadi.

    sudo python3 -m lab.benchmarks.real_calibration --reps 3 --output /tmp/measurements.csv
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
"""

import argparse
import csv
import fcntl
import ipaddress
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time

from lab.drivers.base import EventBus
from lab.drivers.mininet_driver import MininetDriver
from lab.tests.real_integration import load, wait_ping

FIELDS = ["protocol", "metric", "unit", "model", "real", "source", "verified", "abs_tolerance", "rel_tolerance"]
# faqat haqiqiy javoblar ("bytes from"); "Destination Net Unreachable" qatorlari javob emas
_PING = re.compile(r"^\[(\d+\.\d+)\]\s+\d+ bytes from .*icmp_seq=(\d+)")


def poll(fn, timeout, step=0.1):
    """fn() rost bo'lguncha kutadi; o'tgan vaqt (s) yoki None."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if fn():
                return time.time() - t0
        except Exception:  # noqa: BLE001
            pass
        time.sleep(step)
    return None


class Pinger:
    """Hostdan 0.1 s oraliqli ping; javob vaqtlari (epoch) yig'iladi."""

    def __init__(self, drv, host, ip):
        pid = drv.net.get(host).pid
        self.p = subprocess.Popen(["mnexec", "-a", str(pid), "ping", "-D", "-n", "-i", "0.1", "-W", "1", ip],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.times = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            m = _PING.match(line)
            if m:
                self.times.append(float(m.group(1)))

    def stop(self):
        self.p.terminate()
        try:
            self.p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.p.kill()

    def ready_before(self, t_event):
        """Hodisagacha javob kelganmi — aks holda o'lchov yaroqsiz (tarmoq hali tayyor emas)."""
        return any(t <= t_event for t in self.times)

    def gap_after(self, t_event):
        """t_event'dan keyingi eng uzun javobsiz oraliq (s), probe intervali ayirilgan."""
        ts = sorted(self.times)
        before = [t for t in ts if t <= t_event]
        after = [t for t in ts if t > t_event]
        if not before or not after:
            return None
        seq = [before[-1]] + after
        return max(0.0, max(b - a for a, b in zip(seq, seq[1:])) - 0.1)


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
    pg = Pinger(drv, "h1", h2)
    time.sleep(2)
    t_ev = time.time()
    drv.update_link("l2", up=False)
    fwd = poll(lambda: _ovs_state(drv, "s3", 2, mode) == "forwarding", 90)
    time.sleep(8 if mode == "stp" else 4)
    pg.stop()
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
    pg = Pinger(drv, "pc1", srv)
    time.sleep(2)
    t_ev = time.time()
    drv.update_link("l3", up=False)
    md = poll(lambda: _vrrp_state(drv, "r2") == "Master", 20, step=0.05)
    time.sleep(12)
    pg.stop()
    if not pg.ready_before(t_ev):
        raise RuntimeError("hodisagacha ping javobi yo'q — takror yaroqsiz")
    e = p["devices"]["r2"]["config"]["vrrp"][0]
    model = mdi(e)
    return [("vrrp", "master_down_s", model, md, 1.0, 0.2), ("vrrp", "traffic_gap_s", model, pg.gap_after(t_ev), 3.0, 0.5)]


def _route_via(drv, r, dst):
    out = drv.net.get(r).cmd(f"ip -4 route get {dst} 2>/dev/null")
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
    before = _route_via(drv, "r1", srv)
    pg = Pinger(drv, "pc1", srv)
    time.sleep(2)
    t_ev = time.time()
    drv.update_link("l2", up=False)
    rec = poll(lambda: _route_via(drv, "r1", srv) not in (None, before), update * 4 + 10)   # yangi qo'shni orqali
    time.sleep(3)
    pg.stop()
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
    has = lambda: bool(drv.net.get("r3").cmd("ip -4 route show 10.1.0.0/24").strip())  # noqa: E731
    drv.update_link("l4", up=False)
    if poll(lambda: not has(), 20) is None:
        raise RuntimeError("bgp: marshrut olib tashlanmadi")
    time.sleep(2)
    pg = Pinger(drv, "pc1", srv)
    t_ev = time.time()
    drv.update_link("l4", up=True)
    rec = poll(has, 60)
    time.sleep(3)
    pg.stop()
    # link up -> ping qaytguncha (bu yerda gap = hodisadan birinchi javobgacha)
    first = next((t for t in sorted(pg.times) if t > t_ev), None)
    return [("bgp", "session_recovery_s", 2.0, rec, 2.0, 0.5),
            ("bgp", "traffic_recovery_s", 2.0, (first - t_ev) if first else None, 3.0, 0.5)]


def m_ospf(drv):
    from lab.topogen import generate
    p = generate("ring", routers=4, hosts=1, flows=0)
    drv.start(p)
    dst = _ip(p, "h1x0")
    if wait_ping(drv, "h0x0", dst, 120) is None:
        raise RuntimeError("ospf: boshlang'ich holat tayyor emas")
    before = _route_via(drv, "r0", dst)
    pg = Pinger(drv, "h0x0", dst)
    time.sleep(2)
    t_ev = time.time()
    drv.update_link("l1", up=False)        # r0-r1
    upd = poll(lambda: _route_via(drv, "r0", dst) not in (None, before), 30, step=0.05)   # muqobil yo'l paydo bo'ldi
    time.sleep(4)
    pg.stop()
    if not pg.ready_before(t_ev):
        raise RuntimeError("hodisagacha ping javobi yo'q — takror yaroqsiz")
    # analitik model: SPF darhol (0 s); hello 1 s / dead 4 s, interfeys o'chishi — darhol aniqlanadi
    return [("ospf", "route_update_s", 0.0, upd, 1.0, 0.0), ("ospf", "traffic_gap_s", 0.0, pg.gap_after(t_ev), 1.5, 0.0)]


MEASURES = {"stp": lambda d: m_stp(d, "stp"), "rstp": lambda d: m_stp(d, "rstp"), "vrrp": m_vrrp,
            "rip": m_rip, "bgp": m_bgp, "ospf": m_ospf}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", nargs="+", choices=sorted(MEASURES), default=sorted(MEASURES))
    ap.add_argument("--output", required=True)
    a = ap.parse_args(argv)
    if os.geteuid() != 0:
        print("root kerak")
        return 2
    run = time.strftime("%Y%m%dT%H%M%S")
    host = f"{socket.gethostname()}/{platform.release()}"
    rows = []
    with open("/tmp/netlab-real.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        drv = MininetDriver(EventBus())
        caps = drv.capabilities()
        for name in a.only:
            for rep in range(1, a.reps + 1):
                print(f"== {name} #{rep}", flush=True)
                try:
                    for proto, metric, model, real, abs_t, rel_t in MEASURES[name](drv):
                        print(f"   {proto} {metric}: model {model:.3f} real {real if real is None else round(real, 3)}", flush=True)
                        if real is None:
                            continue           # o'lchanmadi — CSV'ga uydirma qiymat yozilmaydi
                        rows.append({"protocol": proto, "metric": metric, "unit": "s", "model": round(model, 4),
                                     "real": round(real, 4), "verified": "true",
                                     "source": f"host={host} datapath={caps.get('ovs_datapath')} netem={caps.get('netem')} "
                                               f"run={run} test={name} rep={rep}",
                                     "abs_tolerance": abs_t, "rel_tolerance": rel_t})
                except Exception as e:  # noqa: BLE001
                    print(f"   XATO: {e}", flush=True)
                finally:
                    try:
                        drv.stop()
                    except Exception:  # noqa: BLE001
                        pass
    with open(a.output, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} o'lchov -> {a.output}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
