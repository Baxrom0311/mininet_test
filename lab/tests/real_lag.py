"""Real Mininet testi: link aggregation (OVS bond + LACP) — root kerak.

    sudo python3 -u -m lab.tests.real_lag
"""

import os
import re
import sys
import time

from lab.real_lock import serial_real_test
from lab.tests.real_integration import FAILS, EventBus, MininetDriver, check, load, ping, wait_ping


def _iperf_pair(drv, pairs, secs=6):
    """Parallel TCP: [(client, server_ip, port)] -> {client: Mbit/s}."""
    for i, (cli, sip, port) in enumerate(pairs):
        srv = next(n for n, d in drv.project["devices"].items()
                   if any((ic.get("ip") or "").split("/")[0] == sip
                          for ic in ((d.get("config") or {}).get("interfaces") or {}).values()))
        drv.net.get(srv).cmd(f"iperf3 -s -1 -D -p {port}")
    time.sleep(0.5)
    for cli, sip, port in pairs:
        drv.net.get(cli).cmd(f"iperf3 -c {sip} -p {port} -t {secs} -f m > /tmp/netlab-lag-{cli}-{port}.txt 2>&1 &")
    time.sleep(secs + 2.5)
    out = {}
    for cli, _sip, port in pairs:
        try:
            txt = open(f"/tmp/netlab-lag-{cli}-{port}.txt").read()
        except OSError:
            txt = ""
        m = re.findall(r"([\d.]+) Mbits/sec\s+\d*\s*receiver", txt)
        out[f"{cli}:{port}"] = float(m[-1]) if m else 0.0
    return out


def t_lag(drv, caps):
    """OVS bond (balance-tcp, LACP active) s1<->s2 (2x100 Mbit/s): a'zolar, LACP, STP bloklamaydi,
    parallel oqimlar taqsimlanishi, a'zo uzilishida trafik davom etadi."""
    import shutil
    proj = load("lag_lab")
    drv.start(proj)
    s1 = drv.net.get("s1")
    bs = s1.cmd("ovs-appctl bond/show s1-bond1")
    print("    bond/show:", " | ".join(x.strip() for x in bs.splitlines() if x.strip())[:400])
    check("s1-eth3" in bs and "s1-eth4" in bs, "bond s1-bond1: ikkala a'zo (s1-eth3, s1-eth4)")
    t0 = time.time()
    lacp = ""
    while time.time() - t0 < 15:
        lacp = s1.cmd("ovs-appctl lacp/show s1-bond1")
        if lacp.count("current") >= 2 or "negotiated" in lacp:
            break
        time.sleep(1)
    st = re.findall(r"(?m)^\s*(?:status|lacp_status):\s*(\S+)", lacp)
    print("    lacp:", st, "| slave/member qatorlar:", [x.strip() for x in lacp.splitlines() if "slave" in x or "member" in x][:4])
    check("negotiated" in lacp, f"LACP muzokara qilindi ({'negotiated' in lacp})")
    en = len(re.findall(r"(?:member|slave) s1-eth[34]: enabled", bs + s1.cmd("ovs-appctl bond/show s1-bond1")))
    check(en >= 2, f"ikkala a'zo enabled ({en})")
    stp_en = s1.cmd("ovs-vsctl get bridge s1 stp_enable rstp_enable").split()
    check(stp_en == ["false", "false"], f"bond'li bridge'da STP o'chirilgan (OVS cheklovi): {stp_en}")
    check(wait_ping(drv, "h1", "10.0.0.3", 15) is not None, "h1 -> h3 ping (bond orqali, storm yo'q)")
    check(wait_ping(drv, "h2", "10.0.0.4", 10) is not None, "h2 -> h4 ping")
    if shutil.which("iperf3"):
        single = _iperf_pair(drv, [("h1", "10.0.0.3", 5301)], secs=4)
        print(f"    bitta oqim: {single}")
        txb = lambda: [int(x) for x in s1.cmd("cat /sys/class/net/s1-eth3/statistics/tx_bytes "  # noqa: E731
                                                  "/sys/class/net/s1-eth4/statistics/tx_bytes").split()]
        b0 = txb()
        res = _iperf_pair(drv, [("h1", "10.0.0.3", 5302), ("h1", "10.0.0.3", 5303),
                                ("h2", "10.0.0.4", 5304), ("h2", "10.0.0.4", 5305)])
        b1 = txb()
        agg = sum(res.values())
        share = [round((b - a) / 1e6, 1) for a, b in zip(b0, b1)]
        print(f"    4 parallel oqim: {res}, jami {agg:.1f} Mbit/s; a'zolar orqali MB: s1-eth3 {share[0]}, s1-eth4 {share[1]}")
        check(agg > 60, f"parallel oqimlar ishladi (jami {agg:.1f} Mbit/s)")
        both = min(share) > 0.2 * max(share)
        if both:
            check(agg > 1.3 * max(single.values()),
                  f"balance-tcp: oqimlar ikkala a'zoga taqsimlandi, jami {agg:.0f} > bitta kabel {max(single.values()):.0f} Mbit/s")
        else:
            print(f"    ESLATMA: hash barcha oqimlarni bitta a'zoga qo'ydi (MB {share}) — jami {agg:.0f} Mbit/s")
    # a'zo uzilishi: trafik qolgan a'zo orqali davom etadi
    drv.update_link("l5", up=False)
    t1 = time.time()
    dt = wait_ping(drv, "h1", "10.0.0.3", 20)
    dt2 = wait_ping(drv, "h2", "10.0.0.4", 20)
    print(f"    l5 uzildi: h1->h3 {dt and round(dt, 2)} s, h2->h4 {dt2 and round(dt2, 2)} s ({time.time() - t1:.1f} s)")
    check(dt is not None and dt2 is not None, "a'zo (l5) uzilsa trafik l6 orqali davom etadi")
    bs2 = s1.cmd("ovs-appctl bond/show s1-bond1")
    check(re.search(r"(?:member|slave) s1-eth3: disabled", bs2) is not None, "bond/show: s1-eth3 disabled")
    drv.update_link("l5", up=True)
    time.sleep(3)
    bs3 = s1.cmd("ovs-appctl bond/show s1-bond1")
    check(re.search(r"(?:member|slave) s1-eth3: enabled", bs3) is not None, "l5 tiklandi: s1-eth3 yana enabled")
    ins = drv.inspect("s1")
    check(any(sec["title"].startswith("Bond") for sec in ins["sections"]), "inspect: Bond bo'limlari")


TESTS = {"lag": t_lag}


@serial_real_test
def main(argv):
    if os.geteuid() != 0:
        print("root kerak")
        return 2
    drv = MininetDriver(EventBus())
    caps = drv.capabilities()
    print("caps:", caps)
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
    print("\nNATIJA:", "OK" if not FAILS else f"{len(FAILS)} xato: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
