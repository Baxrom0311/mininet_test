"""BGP (ikki AS, eBGP + iBGP) — real Mininet + FRR integratsiya testi (root kerak).

    sudo python3 -m lab.tests.real_bgp
"""

import os
import sys
import time

from lab.real_lock import serial_real_test
from lab.drivers.base import EventBus
from lab.drivers.mininet_driver import MininetDriver
from lab.tests.real_integration import FAILS, check, load, ping, wait_ping  # noqa: F401


def t_bgp(drv, caps):
    import shutil
    if not caps["frr"]:
        print("  skip (FRR yo'q)")
        return
    proj = load("bgp_lab")
    drv.start(proj)
    t0 = time.time()
    dt = wait_ping(drv, "pc1", "10.2.0.10", 90)
    check(dt is not None, f"pc1 -> srv1 (AS65001 -> AS65002) ({dt and round(dt, 1)} s)")
    summ = drv.net.get("r2").cmd("vtysh -N r2 -c 'show bgp summary'")
    est = [ln for ln in summ.splitlines() if ln.strip().startswith(("10.12.0.1", "203.0.113.2"))]
    print("    r2 bgp summary:", " | ".join(" ".join(x.split()[:3] + x.split()[-2:]) for x in est))
    check(len(est) == 2 and all(x.split()[-2].isdigit() or x.split()[-1].isdigit() for x in est),
          "r2: iBGP (r1) va eBGP (r3) sessiyalari Established (PfxRcd raqam)")
    r1 = drv.net.get("r1").cmd("vtysh -N r1 -c 'show ip route 10.2.0.0/24'")
    check("bgp" in r1.lower() and "10.12.0.2" in r1, "r1: 10.2.0.0/24 iBGP orqali (next-hop-self 10.12.0.2)")
    transit = drv.net.get("r1").cmd("ip -color=never -4 route show")
    check(not any(line.split()[0] == "203.0.113.0/30" for line in transit.splitlines() if line.strip()),
          "r1: e'lon qilinmagan 203.0.113.0/30 OSPF marshruti yo'q")
    r3 = drv.net.get("r3").cmd("vtysh -N r3 -c 'show ip bgp 10.1.0.0/24'")
    check("65001" in r3, "r3: 10.1.0.0/24 AS_PATH 65001")
    ins = drv.inspect("r2")
    check(any(s["title"] == "FRR: show ip bgp" for s in ins["sections"]), "inspect r2: 'FRR: show ip bgp'")
    # eBGP kabeli uziladi -> marshrut olib tashlanadi; tiklanadi -> qayta o'rnatiladi
    drv.update_link("l4", up=False)
    t1 = time.time()
    gone = None
    while time.time() - t1 < 30:
        if "10.2.0.0/24" not in drv.net.get("r1").cmd("ip route"):
            gone = time.time() - t1
            break
        time.sleep(0.2)
    check(gone is not None, f"l4 uzildi -> r1'dan 10.2.0.0/24 olib tashlandi ({gone and round(gone, 2)} s)")
    drv.update_link("l4", up=True)
    back = wait_ping(drv, "pc1", "10.2.0.10", 120)
    check(back is not None, f"l4 tiklandi -> pc1 -> srv1 qayta ({back and round(back, 1)} s)")
    # emulyatsiya: ssenariy (uzilish + tiklanish) — marshrut hodisalari va konvergensiya
    if shutil.which("iperf3"):
        from lab.engines import emulation
        time.sleep(2)
        sc = dict(proj["scenarios"]["ebgp_uzilish"])
        res = emulation.run(drv, sc)
        v = {int(p["t"]): p["thr_bps"] for p in res["flows"]["video"]["series"]}
        gap = sorted(t for t in range(11, 39) if v.get(t, 0) < 1e5)
        print("    video gap (s):", gap[:3], "...", gap[-3:], "len", len(gap))
        print("    convergence:", [(c["action"], c["converged_s"]) for c in res["convergence"]])
        check(v.get(5, 0) > 3e6 and v.get(38, 0) > 3e6 and 8 <= len(gap) <= 25,
              f"emulyatsiya: uzilishda video to'xtaydi ({len(gap)} s), tiklangach qaytadi")
        cv = {c["action"]: c["converged_s"] for c in res["convergence"]}
        check(cv.get("link_up") is not None, f"link_up -> BGP konvergensiyasi o'lchandi: {cv.get('link_up')} s")


@serial_real_test
def main():
    if os.geteuid() != 0:
        print("root kerak")
        return 2
    drv = MininetDriver(EventBus())
    caps = drv.capabilities()
    print("== bgp")
    t0 = time.time()
    try:
        t_bgp(drv, caps)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, f"bgp: istisno {e}")
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
