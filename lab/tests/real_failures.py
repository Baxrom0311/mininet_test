"""Nosozlik/tiklanish (MTBF/MTTR) — real Mininet emulyatsiyasi (root kerak).

    flock /tmp/netlab-real.lock sudo python3 -m lab.tests.real_failures
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lab.tests.real_integration import check, host, link  # noqa: E402


def t_failures(drv, caps):
    """h1 - s1 - h2: l2 kabeliga qat'iy MTBF 10 s / MTTR 5 s — real link down/up, ping yo'qotilishi,
    mavjudlik model bilan solishtiriladi; qurilma (s1) nosozligi — barcha kabellari."""
    import shutil
    from lab import failures as F
    from lab.engines import emulation
    if not shutil.which("iperf3"):
        print("  skip (iperf3 yo'q)")
        return
    proj = {"version": 1, "name": "rf", "devices": {
        "h1": host("h1", 1, "10.0.0.1/24"), "h2": host("h2", 2, "10.0.0.2/24"),
        "s1": {"id": 3, "type": "switch", "x": 0, "y": 0, "config": {"mode": "standalone", "stp": False}}},
        "links": {"l1": link("h1", 1, "s1", 1), "l2": link("s1", 2, "h2", 1)},
        "p4_programs": {}, "controllers": {}, "scenarios": {}}
    sc = {"duration": 40, "apps": [
        {"id": "p", "type": "ping", "src": "h1", "dst": "h2", "interval": 0.2, "start": 1, "stop": 39},
        {"id": "u", "type": "udp", "src": "h1", "dst": "h2", "rate": "2Mbps", "size": 1000, "start": 1, "stop": 39}],
        "events": [], "failures": {"seed": 7, "distribution": "fixed",
                                   "links": {"mtbf_s": 10, "mttr_s": 5, "targets": ["l2"]}}}
    ex = F.expand(proj, sc)
    gen = [e for e in ex["events"] if e.get("source") == "failure"]
    print("    hodisalar:", [(e["t"], e["action"]) for e in gen])
    drv.start(proj)
    res = F.postprocess(emulation.run(drv, ex), sc, ex, proj)
    av = res["availability"]
    applied = [e for e in res["events"] if e.get("source") == "failure"]
    check(len(applied) == len(gen) == 4, f"{len(applied)}/{len(gen)} nosozlik hodisasi realda qo'llandi")
    ping = {int(p["t"]): p for p in res["flows"]["p"]["series"]}
    lost = [t for t in (12, 13, 14, 27, 28, 29) if ping.get(t, {}).get("rtt_ms") is not None]
    ok_ = [t for t in (5, 8, 20, 23, 35) if ping.get(t, {}).get("rtt_ms") is None]
    check(not lost and not ok_, f"ping: uzilishlarda (10-15, 25-30 s) javob yo'q, boshqa paytda bor "
                                f"(ortiqcha javob {lost}, yo'qolgan {ok_})")
    pa, ua = av["flows"]["p"]["availability_pct"], av["flows"]["u"]["availability_pct"]
    model = 100 - av["links"]["l2"]["downtime_pct"]
    print(f"    mavjudlik: ping {pa}%, udp {ua}%, l2 ishlagan {model}%, nazariy {av['theoretical']['l2']}%")
    check(abs(pa - model) < 8 and abs(ua - model) < 8, f"real mavjudlik ≈ kabel ishlagan vaqt ({model}%)")
    check(len(av["flows"]["p"]["outages"]) == 2, f"ping uzilishlari: {av['flows']['p']['outages']}")
    drv.stop()
    # qurilma nosozligi: s1 -> l1 + l2
    sc2 = {"duration": 16, "apps": [{"id": "p", "type": "ping", "src": "h1", "dst": "h2", "interval": 0.2,
                                     "start": 1, "stop": 15}], "events": [],
           "failures": {"seed": 1, "distribution": "fixed", "devices": {"mtbf_s": 6, "mttr_s": 4, "targets": ["s1"]}}}
    ex2 = F.expand(proj, sc2)
    drv.start(proj)
    res2 = F.postprocess(emulation.run(drv, ex2), sc2, ex2, proj)
    links2 = res2["availability"]["links"]
    ping2 = {int(p["t"]): p for p in res2["flows"]["p"]["series"]}
    check(set(links2) == {"l1", "l2"} and all(ping2.get(t, {}).get("rtt_ms") is None for t in (8, 9))
          and ping2.get(4, {}).get("rtt_ms") is not None and ping2.get(13, {}).get("rtt_ms") is not None,
          f"qurilma s1 nosozligi: l1+l2 uzildi (6-10 s), ping {res2['availability']['flows']['p']['availability_pct']}%")


if __name__ == "__main__":
    from lab.drivers.base import EventBus
    from lab.drivers.mininet_driver import MininetDriver
    from lab.tests import real_integration as RI
    if os.geteuid() != 0:
        print("root kerak")
        sys.exit(2)
    drv = MininetDriver(EventBus())
    caps = drv.capabilities()
    print("== failures", flush=True)
    try:
        t_failures(drv, caps)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, f"istisno {e}")
    finally:
        drv.stop()
    print("\nNATIJA:", "OK" if not RI.FAILS else f"{len(RI.FAILS)} xato: {RI.FAILS}")
    sys.exit(1 if RI.FAILS else 0)
