"""VRRP (r1/r2 virtual gateway) — real Mininet + FRR vrrpd integratsiya testi (root kerak).

    sudo python3 -m lab.tests.real_vrrp
"""

import os
import re
import shutil
import sys
import time

from lab.real_lock import serial_real_test
from lab.drivers.base import EventBus
from lab.drivers.mininet_driver import MininetDriver
from lab.tests.real_integration import FAILS, check, load, ping, wait_ping  # noqa: F401

VIP = "10.0.1.254"


def _state(drv, r):
    out = drv.net.get(r).cmd(f"vtysh -N {r} -c 'show vrrp' 2>&1")
    m = re.search(r"Status \(v4\)\s+(\w+)", out)
    return (m.group(1) if m else "?"), out


def _wait_state(drv, r, want, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, _ = _state(drv, r)
        if st == want:
            return time.time() - t0
        time.sleep(0.25)
    return None


def t_vrrp(drv, caps):
    if not caps["frr"]:
        print("  skip (FRR yo'q)")
        return
    proj = load("vrrp_lab")
    drv.start(proj)
    check(_wait_state(drv, "r1", "Master", 20) is not None, "r1: VRRP Master (priority 120)")
    st2, out2 = _state(drv, "r2")
    check(st2 == "Backup", f"r2: VRRP {st2} (Backup kutilgan)")
    if st2 != "Backup":
        print(out2[-600:])
    check(wait_ping(drv, "pc1", "10.0.3.10", 60) is not None, "pc1 -> srv1 VIP gateway orqali (OSPF tayyor)")
    arp = drv.net.get("pc1").cmd(f"ip neigh show {VIP}")
    check("00:00:5e:00:01:01" in arp, f"pc1 ARP: VIP -> VRRP virtual MAC ({arp.strip()})")
    ins = drv.inspect("r1")
    check(any(s["title"] == "FRR: show vrrp" for s in ins["sections"]), "inspect r1: FRR show vrrp bo'limi")

    # failover: r1 LAN kabeli uziladi -> r2 Master_Down_Interval (~3.6 s) dan keyin master
    t_down = time.time()
    drv.update_link("l3", up=False)
    dt = _wait_state(drv, "r2", "Master", 15)
    check(dt is not None and dt < 8, f"failover: r2 Master {dt and round(dt, 2)} s da (MDI ~3.6 s)")
    gap = wait_ping(drv, "pc1", "10.0.3.10", 30)
    check(gap is not None, f"failover dan keyin pc1 -> srv1 ({round(time.time() - t_down, 1)} s uzilishdan keyin)")
    # preempt: r1 qaytadi -> o'z MDI'sidan keyin yana master
    drv.update_link("l3", up=True)
    dp = _wait_state(drv, "r1", "Master", 20)
    check(dp is not None, f"preempt: r1 yana Master ({dp and round(dp, 2)} s)")
    check(_wait_state(drv, "r2", "Backup", 10) is not None, "r2 Backup'ga qaytdi")
    check(wait_ping(drv, "pc1", "10.0.3.10", 20) is not None, "preempt dan keyin ping ishlaydi")

    # emulyatsiya: ping uzilishi va konvergensiya (ssenariy zaxira)
    if shutil.which("iperf3"):
        from lab.engines import emulation
        time.sleep(3)
        sc = dict(proj["scenarios"]["zaxira"])
        res = emulation.run(drv, sc)
        pg = {int(x["t"]): x.get("loss_pct") for x in res["flows"]["ping"]["series"]}
        lost = sorted(t for t, l in pg.items() if l is not None and l >= 50)
        print("    ping yo'qotish (>=50%) soniyalari:", lost)
        # VRRP failover (~3.6 s) + javob yo'lida OSPF (LAN dead-interval 4 s) konvergensiyasi
        check(lost and all(10 <= t <= 20 for t in lost) and len(lost) <= 9,
              f"emulyatsiya: uzilish faqat failover paytida ({len(lost)} s), preempt'da uzilish yo'q")
        cv = {c["action"]: c["converged_s"] for c in res["convergence"]}
        print("    convergence:", res["convergence"])
        check(cv.get("link_down") is not None, f"link_down konvergensiya o'lchandi: {cv.get('link_down')} s")


@serial_real_test
def main():
    if os.geteuid() != 0:
        print("root kerak")
        return 2
    drv = MininetDriver(EventBus())
    caps = drv.capabilities()
    print("== vrrp")
    t0 = time.time()
    try:
        t_vrrp(drv, caps)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, f"vrrp: istisno {e}")
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
