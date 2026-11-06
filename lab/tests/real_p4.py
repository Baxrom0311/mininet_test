"""Strict P4 compile/runtime gate; missing tools return blocked (exit 2)."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile

from lab.drivers.base import EventBus, p4_compile_standalone, p4_pipeline_from_json


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--compile-only", action="store_true")
    ap.add_argument("--output", type=Path, default=Path("/tmp/netlab-real-p4.json"))
    args = ap.parse_args(argv)
    compiler = shutil.which("p4c-bm2-ss") or shutil.which("p4c")
    missing = [] if compiler else ["p4c compiler missing"]
    report = {"status": "blocked", "missing": missing, "compilation": [], "runtime": "not_run"}
    with tempfile.TemporaryDirectory(prefix="netlab-p4-") as td:
        if compiler:
            for source in sorted((Path(__file__).resolve().parents[1] / "templates/p4").glob("*.p4")):
                result = p4_compile_standalone(source.read_text(), str(Path(td, source.stem)))
                if result["ok"]:
                    result["tables"] = [t["name"] for p in p4_pipeline_from_json(result["json_path"])["pipelines"] for t in p["tables"]]
                result.pop("json_path", None)
                report["compilation"].append({"template": source.name, **result})
            report["status"] = "passed" if report["compilation"] and all(r["ok"] for r in report["compilation"]) else "failed"
        if not args.compile_only:
            if os.geteuid() != 0:
                missing.append("root required")
            if importlib.util.find_spec("mininet") is None:
                missing.append("Python mininet missing")
            for tool in ("mnexec", "ovs-vsctl", "ping", "simple_switch", "simple_switch_CLI"):
                if not shutil.which(tool):
                    missing.append(f"{tool} missing")
            if missing and report["status"] != "failed":
                report["status"] = "blocked"
            elif not missing and report["status"] == "passed":
                import fcntl
                from lab.drivers.mininet_driver import MininetDriver
                from lab.tests.real_integration import FAILS, t_p4
                driver = MininetDriver(EventBus())
                driver.capture_on = False
                with open("/tmp/netlab-real.lock", "a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    before = len(FAILS)
                    try:
                        t_p4(driver, driver.capabilities())
                        report["runtime"] = "passed" if len(FAILS) == before else "failed"
                        report["status"] = report["runtime"]
                    except Exception as e:
                        report.update(status="failed", runtime="failed", error=f"{type(e).__name__}: {e}")
                    finally:
                        driver.stop()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return {"passed": 0, "failed": 1, "blocked": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
