"""Multicast (IGMP + PIM-SM, FRR pimd) — real Mininet integratsiya testi (root kerak).

    sudo python3 -m lab.tests.real_mcast
"""

import os
import sys
import time

from lab.real_lock import serial_real_test
from lab.drivers.base import EventBus
from lab.drivers.mininet_driver import MininetDriver
from lab.tests.real_integration import FAILS, check, load, wait_ping  # noqa: F401


def t_mcast(drv, caps):
    from lab import apps as am
    from lab.engines import emulation
    if not caps["frr"] or not os.path.exists("/usr/lib/frr/pimd"):
        print("  skip (FRR pimd yo'q)")
        return
    proj = load("mcast_lab")
    drv.start(proj)
    for dst in ("10.0.2.11", "10.0.3.13"):
        check(wait_ping(drv, "srv1", dst, 60) is not None, f"OSPF: srv1 -> {dst}")
    time.sleep(3)
    nb = drv.net.get("r1").cmd("vtysh -N r1 -c 'show ip pim neighbor'")
    check(nb.count("10.0.") >= 2, f"r1 PIM qo'shnilari: {[ln.split()[1] for ln in nb.splitlines() if '10.0.' in ln][:3]}")
    rp = drv.net.get("r3").cmd("vtysh -N r3 -c 'show ip pim rp-info'")
    check("10.0.0.1" in rp, "r3 RP = 10.0.0.1 (r1)")
    sc = {"duration": 30, "apps": [
        {"id": "tv", "type": "mcast", "src": "srv1", "group": "239.1.1.1", "dst": ["pc1", "pc3"], "rate": "4Mbps",
         "size": 1000, "start": 2, "stop": 29}],
        "events": [{"t": 14, "action": "link_down", "link": "l3"}]}
    mr = {}

    def snap():
        time.sleep(9)
        mr["r1"] = drv.net.get("r1").cmd("vtysh -N r1 -c 'show ip mroute'")
        mr["r2"] = drv.net.get("r2").cmd("vtysh -N r2 -c 'show ip igmp groups'")
        mr["pc2"] = drv.net.get("pc2").cmd("timeout 2 tcpdump -nni pc2-eth1 -c 50 udp port 5600 2>/dev/null | wc -l")
        mr["pc2_n"] = drv.net.get("pc2").cmd("cat /proc/net/dev | grep pc2-eth1")
    import threading
    th = threading.Thread(target=snap, daemon=True)
    th.start()
    res = am.postprocess(emulation.run(drv, am.expand(sc)), sc)
    th.join(timeout=5)
    a = res["apps"]["tv"]
    print("   ", {k: v for k, v in a.items() if k != "per_receiver"})
    print("    per_receiver", a["per_receiver"])
    check("239.1.1.1" in mr.get("r1", ""), "r1 show ip mroute: (S,G)/(*,G) 239.1.1.1 bor")
    check("239.1.1.1" in mr.get("r2", ""), "r2 IGMP guruhlari: 239.1.1.1 (pc1 a'zo)")
    pc1 = [p for p in res["flows"]["tv.pc1"]["series"] if 6 <= p["t"] <= 13]
    pc3 = [p for p in res["flows"]["tv.pc3"]["series"] if 6 <= p["t"] <= 13]
    thr1 = sum(p["thr_bps"] for p in pc1) / max(len(pc1), 1)
    thr3 = sum(p["thr_bps"] for p in pc3) / max(len(pc3), 1)
    check(thr1 > 3.0e6 and thr3 > 3.0e6, f"pc1/pc3 oqim oladi: {thr1 / 1e6:.2f} / {thr3 / 1e6:.2f} Mbit/s")
    pc2_cnt = int((mr.get("pc2") or "0").strip() or 0)
    check(pc2_cnt < 5, f"IGMP snooping: a'zo bo'lmagan pc2 ga multicast yetmaydi ({pc2_cnt} paket / 2 s)")
    l2 = [p["ab_bps"] for p in res["links"]["l2"] if 6 <= p["t"] <= 13]
    l2a = sum(l2) / max(len(l2), 1)
    check(3.0e6 < l2a < 5.5e6, f"r1-r2 kabelida bitta nusxa: {l2a / 1e6:.2f} Mbit/s")
    after = [p["thr_bps"] for p in res["flows"]["tv.pc3"]["series"] if 24 <= p["t"] <= 28]
    gap = [int(p["t"]) for p in res["flows"]["tv.pc3"]["series"] if 14 < p["t"] <= 28 and p["thr_bps"] < 1e5]
    print(f"    pc3 l3 uzilgandan keyin oqimsiz soniyalar: {gap}")
    check(after and min(after) > 3.0e6, f"l3 uzilgandan keyin pc3 r2-r3 orqali tiklandi (uzilish {len(gap)} s)")
    l4 = [p["ab_bps"] for p in res["links"]["l4"] if 17 <= p["t"] <= 27]
    l4b = [p["ab_bps"] for p in res["links"]["l4"] if 6 <= p["t"] <= 13]
    check(l4 and sum(l4) / len(l4) > 3.0e6 and (sum(l4b) / max(len(l4b), 1)) < 1e6,
          f"l4 (r2->r3): oldin {sum(l4b) / max(len(l4b), 1) / 1e6:.2f}, keyin {sum(l4) / max(len(l4), 1) / 1e6:.2f} Mbit/s")
    print("    pc3 t=13..17:", [(p["t"], round(p["thr_bps"] / 1e6, 2)) for p in res["flows"]["tv.pc3"]["series"] if 13 <= p["t"] <= 17])


@serial_real_test
def main():
    if os.geteuid() != 0:
        print("root kerak")
        return 2
    drv = MininetDriver(EventBus())
    caps = drv.capabilities()
    print("== mcast")
    t0 = time.time()
    try:
        t_mcast(drv, caps)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, f"mcast: istisno {e}")
    finally:
        try:
            drv.stop()
        except Exception as e:  # noqa: BLE001
            check(False, f"stop {e}")
    print(f"   ({time.time() - t0:.1f} s)")
    print("\nNATIJA:", "OK" if not FAILS else f"{len(FAILS)} xato: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
