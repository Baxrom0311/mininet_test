"""Firewall/ACL real Mininet testi (root + Mininet + OVS; iptables router namespace'ida).

    sudo python3 -u -m lab.tests.real_fw
"""

import copy
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lab.real_lock import serial_real_test
from lab.drivers.base import EventBus  # noqa: E402
from lab.drivers.mininet_driver import MininetDriver  # noqa: E402
from lab.tests import real_integration as ri  # noqa: E402
from lab.tests.real_integration import check, load, ping, wait_ping  # noqa: E402


def tcp_ok(drv, src, dst, ip, port, secs=2):
    """iperf3 TCP sinovi: (o'tdimi, Mbit/s)."""
    drv.net.get(dst).cmd(f"iperf3 -s -1 -D -p {port}")
    time.sleep(0.4)
    out = drv.net.get(src).cmd(f"timeout 8 iperf3 -c {ip} -p {port} -t {secs} -f m --connect-timeout 2000 2>&1")
    drv.net.get(dst).cmd(f"pkill -f 'iperf3 -s -1 -D -p {port}' 2>/dev/null")
    import re
    m = re.findall(r"([\d.]+) Mbits/sec\s+\d*\s*receiver", out)
    return (bool(m), float(m[-1]) if m else 0.0, out.strip().splitlines()[-1:] if out.strip() else [])


def t_fw(drv, caps):
    """Stateful ACL: ruxsat/blok (TCP portlar, ICMP), reject vs drop, established, hisoblagichlar, jonli o'zgarish."""
    if not shutil.which("iperf3"):
        print("  skip (iperf3 yo'q)")
        return
    proj = load("firewall_lab")
    drv.start(proj)
    rules = drv.net.get("r1").cmd("iptables -S NETLAB-FWD")
    print("    " + rules.strip().replace("\n", "\n    "))
    check("-j DROP" in rules and "ESTABLISHED" in rules, "r1: NETLAB-FWD zanjiri (established + default drop)")
    check(drv.net.get("r1").cmd("iptables -C FORWARD -j NETLAB-FWD && echo yes").strip().endswith("yes"),
          "FORWARD -> NETLAB-FWD sakrash")
    check(wait_ping(drv, "pc1", "203.0.113.50", 10) is not None, "ICMP: pc1 -> inet (LAN chiqadi, javob established)")
    out = drv.net.get("inet").cmd("ping -c 2 -W 1 10.0.1.10")
    check("100% packet loss" in out and "Prohibited" in out, f"ICMP: inet -> pc1 reject ({'Prohibited' in out})")
    check(not ping(drv, "inet", "10.0.2.10", count=2, wait=1)[0], "ICMP: inet -> srv1 drop")
    ok, mb, _ = tcp_ok(drv, "inet", "srv1", "10.0.2.10", 80)
    check(ok and mb > 10, f"TCP: inet -> srv1:80 ruxsat ({mb} Mbit/s)")
    ok, _mb, tail = tcp_ok(drv, "inet", "srv1", "10.0.2.10", 22)
    check(not ok, f"TCP: inet -> srv1:22 bloklangan ({tail})")
    ok, mb, _ = tcp_ok(drv, "pc1", "srv1", "10.0.2.10", 8080)
    check(ok and mb > 10, f"TCP: pc1 -> srv1:8080 (LAN qoidasi) ({mb} Mbit/s)")
    ok, _mb, _ = tcp_ok(drv, "srv1", "pc1", "10.0.1.10", 5555)
    check(not ok, "TCP: srv1 (DMZ) -> pc1 yangi ulanish bloklangan (faqat established qaytadi)")
    ins = drv.inspect("r1")
    tbl = next((s for s in ins["sections"] if s["title"].startswith("Firewall")), None)
    rows = (tbl or {}).get("rows") or []
    print("    hisoblagichlar:", [(r[0], r[1], r[2], r[4]) for r in rows])
    hit = {(r[0], r[1]): r[2] for r in rows}
    check(tbl and hit.get(("forward", 1), 0) > 0 and hit.get(("forward", 3), 0) > 0 and hit.get(("forward", 5), 0) > 0,
          "inspect: qoida hisoblagichlari (established, :80, default drop) > 0")
    # jonli: :22 ga ruxsat qo'shamiz
    p2 = copy.deepcopy(drv.project)
    p2["devices"]["r1"]["config"]["firewall"]["rules"].insert(
        1, {"chain": "forward", "action": "accept", "proto": "tcp", "dst": "10.0.2.10/32", "dport": 22})
    drv.apply_device_config(p2, "r1")
    ok, mb, _ = tcp_ok(drv, "inet", "srv1", "10.0.2.10", 22)
    check(ok, f"jonli: :22 qoidasi qo'shildi -> inet -> srv1:22 o'tadi ({mb} Mbit/s)")
    # firewall olib tashlansa — hammasi ochiq, nat jadvali buzilmaydi
    p3 = copy.deepcopy(drv.project)
    p3["devices"]["r1"]["config"].pop("firewall")
    drv.apply_device_config(p3, "r1")
    check(ping(drv, "inet", "10.0.2.10", count=2, wait=1)[0], "firewall olib tashlandi -> inet -> srv1 ping o'tadi")
    check(drv.net.get("r1").cmd("iptables -S NETLAB-FWD").strip() == "-N NETLAB-FWD", "NETLAB-FWD bo'sh")


def t_fw_emu(drv, caps):
    """Mininet dvigateli: ilova dport'i iperf3 portiga aylanadi — firewall natijasi model bilan bir xil."""
    from lab.engines import analytic, emulation
    if not shutil.which("iperf3"):
        print("  skip (iperf3 yo'q)")
        return
    proj = load("firewall_lab")
    sc = copy.deepcopy(proj["scenarios"]["trafik"])
    sc["duration"] = 10
    for a in sc["apps"]:
        a["stop"] = min(a["stop"], 9)
    drv.start(proj)
    time.sleep(1)
    res = emulation.run(drv, sc)
    model = analytic.run(proj, sc)
    for k in ("web_in", "ssh_in", "lan_out", "lan_web", "ping_out", "ping_in"):
        rs, ms = res["flows"][k]["summary"], model["flows"][k]["summary"]
        def passed(sm, k=k):
            if k.startswith("ping"):
                return sm.get("loss_pct") is not None and sm["loss_pct"] < 50
            return (sm["avg_thr_bps"] or 0) > 1e5
        real_ok, mod_ok = passed(rs), passed(ms)
        check(real_ok == mod_ok, f"{k}: real {'o`tdi' if real_ok else 'bloklandi'}, model "
                                  f"{'o`tdi' if mod_ok else 'bloklandi'} ({rs['avg_thr_bps'] / 1e6:.1f} Mbit/s)")


TESTS = {"fw": t_fw, "fw_emu": t_fw_emu}


@serial_real_test
def main(argv):
    if os.geteuid() != 0:
        print("root kerak")
        return 2
    drv = MininetDriver(EventBus())
    caps = drv.capabilities()
    for name in (argv or list(TESTS)):
        print(f"== {name}", flush=True)
        t0 = time.time()
        try:
            TESTS[name](drv, caps)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(False, f"{name}: istisno {e}")
        finally:
            try:
                drv.stop()
            except Exception as e:  # noqa: BLE001
                check(False, f"{name}: stop {e}")
        print(f"   ({time.time() - t0:.1f} s)")
    print("\nNATIJA:", "OK" if not ri.FAILS else f"{len(ri.FAILS)} xato: {ri.FAILS}")
    return 1 if ri.FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
