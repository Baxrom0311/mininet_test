"""Real: generatsiya qilingan halqa (6 router, FRR OSPF) — hostlar o'zaro yetadi, kabel uzilsa muqobil yo'l.

    sudo python3 -m lab.tests.real_topogen
"""

import ipaddress
import sys

from lab.real_lock import serial_real_test
from lab.tests.real_integration import FAILS, EventBus, MininetDriver, check, wait_ping
from lab.topogen import generate


def t_topogen(drv, caps):
    if not caps["frr"]:
        print("  skip (FRR yo'q)")
        return
    p = generate("ring", routers=6, hosts=1, flows=2)
    drv.start(p)
    hs = sorted(d for d, x in p["devices"].items() if x["type"] == "host")
    ip_of = {h: str(ipaddress.ip_interface(next(iter(p["devices"][h]["config"]["interfaces"].values()))["ip"]).ip) for h in hs}
    a, b = hs[0], hs[3]                       # halqaning qarama-qarshi tomonlari
    dt = wait_ping(drv, a, ip_of[b], 90)
    check(dt is not None, f"generator halqa: {a} -> {b} OSPF orqali ({dt and round(dt, 1)} s)")
    nb = drv.net.get("r0").cmd("vtysh -N r0 -c 'show ip ospf neighbor'")
    check(nb.count("Full") >= 2, f"r0 OSPF qo'shnilari Full: {nb.count('Full')}")
    drv.update_link("l1", up=False)          # r0-r1
    dt = wait_ping(drv, a, ip_of[hs[1]], 30)
    check(dt is not None, f"l1 (r0-r1) uzildi -> {a} -> {hs[1]} halqaning boshqa tomonidan ({dt and round(dt, 1)} s)")


@serial_real_test
def main():
    import os
    if os.geteuid() != 0:
        print("root kerak")
        return 2
    drv = MininetDriver(EventBus())
    caps = drv.capabilities()
    try:
        t_topogen(drv, caps)
    finally:
        drv.stop()
    print("\nNATIJA:", "OK" if not FAILS else f"{len(FAILS)} xato: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
