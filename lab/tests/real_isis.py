"""Opt-in real FRR IS-IS neighbors and alternate path recovery (root required)."""

import argparse
import json
from pathlib import Path
import time

from lab.real_lock import serial_real_test
from lab.benchmarks.real_scale import prerequisites
from lab.drivers.base import EventBus
from lab.drivers.mininet_driver import FRR_BIN, MininetDriver
from lab.tests.real_integration import wait_ping
from lab.tests.smoke_isis import fixture


@serial_real_test
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=Path("/tmp/netlab-real-isis.json"))
    args = ap.parse_args(argv)
    missing = prerequisites()
    if not Path(FRR_BIN, "isisd").is_file():
        missing.append("FRR isisd missing")
    result = {"status": "blocked", "missing": missing}
    if not missing:
        driver = MininetDriver(EventBus())
        driver.capture_on = False
        try:
            start = time.monotonic()
            driver.start(fixture())
            ready = wait_ping(driver, "h0x0", "10.0.2.10", 90)
            if ready is None:
                raise RuntimeError("IS-IS forwarding did not converge")
            result["ready_s"] = time.monotonic() - start
            neighbors = {n: driver.net.get(n).cmd(f"vtysh -N {n} -c 'show isis neighbor'")
                         for n in ("r0", "r1", "r2")}
            if not all(text.count("Up") >= 2 for text in neighbors.values()):
                raise RuntimeError("all six directed IS-IS adjacencies must be Up")
            result["neighbors"] = neighbors
            driver.update_link("l1", up=False)
            recovery = wait_ping(driver, "h0x0", "10.0.2.10", 60)
            if recovery is None:
                raise RuntimeError("alternate-path recovery failed")
            result.update(status="passed", recovery_probe_s=recovery)
        except Exception as e:
            result.update(status="failed", error=f"{type(e).__name__}: {e}")
        finally:
            driver.stop()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "missing": missing}))
    return 2 if missing else int(result["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
