"""STP/RSTP real Mininet testi (OVS) — root + Mininet kerak.

    sudo flock /tmp/netlab-real.lock python3 -m lab.tests.real_stp

Uchburchak (stp_lab): model bashorat qilgan port (s3:2) OVS'da ham blocking bo'lishi,
ping ishlashi, faol kabel uzilganda trafik blocking port orqali tiklanishi va
konvergensiya vaqti (STP ~30 s, RSTP ~1-3 s) o'lchanadi.
"""

import copy
import os
import re
import sys
import time

from lab.tests.real_integration import FAILS, check, load, wait_ping
from lab.drivers.base import EventBus
from lab.drivers.mininet_driver import MininetDriver


def ovs_port_states(drv, sw):
    """{port_no: state} — `ovs-appctl stp/show` yoki `rstp/show` dan."""
    from lab.stp import stp_mode
    d = drv.project["devices"][sw]
    cmd = "rstp/show" if stp_mode(d) == "rstp" else "stp/show"
    out = drv.net.get(sw).cmd(f"ovs-appctl {cmd} {sw}")
    st = {}
    for line in out.splitlines():
        m = re.match(rf"\s*{sw}-eth(\d+)\s+(\w+)\s+(\w+)", line)
        if m:
            # STP: "<name> <role> <state>" yoki "<name> <state> <role>" — ikkalasini qamraymiz
            a, b = m.group(2).lower(), m.group(3).lower()
            states = ("forwarding", "blocking", "discarding", "learning", "listening", "disabled")
            st[int(m.group(1))] = a if a in states else b
    return st, out


def outage(res, app="video"):
    pts = res["flows"][app]["series"]
    return [int(p["t"]) for p in pts if (p.get("thr_bps") or 0) < 1e5]


def t_stp(drv, caps):
    import shutil
    from lab.engines import emulation
    from lab.stp import compute
    if not shutil.which("iperf3"):
        print("  skip (iperf3 yo'q)")
        return
    results = {}
    for mode in ("stp", "rstp"):
        proj = load("stp_lab")
        for n in ("s1", "s2", "s3"):
            proj["devices"][n]["config"]["stp_mode"] = mode
        model = compute(proj)
        print(f"  -- {mode}: model blocking {sorted(model['blocked'])}")
        drv.start(proj)
        t0 = time.time()
        dt = wait_ping(drv, "h1", "10.0.0.2", 90)
        check(dt is not None, f"{mode}: h1 -> h2 ping (boshlang'ich konvergensiya {dt and round(time.time() - t0, 1)} s)")
        st3, raw = ovs_port_states(drv, "s3")
        print("    s3:", raw.strip().splitlines()[-4:] if raw.strip() else raw)
        blocked_real = {("s3", p) for p, v in st3.items() if v in ("blocking", "discarding")}
        for sw in ("s1", "s2"):
            stx, _ = ovs_port_states(drv, sw)
            blocked_real |= {(sw, p) for p, v in stx.items() if v in ("blocking", "discarding")}
        check(blocked_real == model["blocked"], f"{mode}: OVS blocking {sorted(blocked_real)} == model {sorted(model['blocked'])}")
        ins = drv.inspect("s3")
        check(any(s_["title"].startswith("STP") for s_ in ins["sections"]), f"{mode}: inspect s3 STP bo'limi")
        sc = copy.deepcopy(proj["scenarios"]["uzilish"])
        sc["duration"] = 55 if mode == "stp" else 25
        for a in sc["apps"]:
            a["stop"] = sc["duration"] - 1
        sc["events"] = [{"t": 8, "action": "link_down", "link": "l2"}]
        res = emulation.run(drv, sc)
        gap = [t for t in outage(res) if t > 8]
        conv = (max(gap) - 8) if gap else 0
        late = [p["thr_bps"] for p in res["flows"]["video"]["series"] if p["t"] >= sc["duration"] - 6]
        results[mode] = (conv, gap)
        print(f"    {mode}: video uzilishi soniyalari {gap} -> konvergensiya ~{conv} s")
        check(late and min(late) > 3e6, f"{mode}: l2 uzilgandan keyin trafik s2-s3 (avval blocking) orqali tiklandi")
        stx, _ = ovs_port_states(drv, "s3")
        check(stx.get(2) == "forwarding", f"{mode}: s3 port2 endi forwarding ({stx.get(2)})")
        drv.stop()
    if "stp" in results:
        check(20 <= results["stp"][0] <= 45, f"STP konvergensiya {results['stp'][0]} s (802.1D 2x15 s kutilgan)")
    if "rstp" in results:
        check(results["rstp"][0] <= 6, f"RSTP konvergensiya {results['rstp'][0]} s (<= 6 s kutilgan)")


def main():
    if os.geteuid() != 0:
        print("root kerak")
        return 2
    drv = MininetDriver(EventBus())
    try:
        t_stp(drv, drv.capabilities())
    finally:
        try:
            drv.stop()
        except Exception:  # noqa: BLE001
            pass
    print("\nNATIJA:", "OK" if not FAILS else f"{len(FAILS)} xato: {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
