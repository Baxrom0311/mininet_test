"""Temporary demo/analytic server for scenario editor browser regressions."""

import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

import uvicorn

from lab.engines import analytic
from lab.server import create_app
from lab.topogen import generate


def main():
    with tempfile.TemporaryDirectory(prefix="netlab-scenario-ui-") as directory, \
            patch("lab.engines.manager.RESULTS_DIR", directory), \
            patch("lab.server.PROJECTS_DIR", str(Path(directory, "projects"))):
        app = create_app("demo")
        project = generate("ring", routers=3, hosts=1, flows=0, ipv6=True)
        project["name"] = "scenario_review"
        for device in project["devices"].values():
            if device["type"] == "router":
                device["config"]["daemons"].append("pimd")
        project["scenarios"] = {"Muharrir": {"duration": 10, "sample": .5, "apps": [], "events": [], "ospf_convergence_s": .25},
            "Import": {"duration": 10, "apps": [{"id": "tv", "type": "mcast", "src": "h0x0", "dst": ["h1x0", "h2x0"], "group": "239.1.1.1", "rate": "1Mbps", "start": 0, "stop": 10}], "events": [],
                       "failures": {"seed": 7, "distribution": "fixed", "devices": {"mtbf_s": 3, "mttr_s": 1, "targets": ["r1"]}}}}
        original = analytic.run
        stopped = threading.Event()

        def run(project, scenario, progress=lambda value: None, log=lambda value: None):
            if scenario.get("notes") == "browser-held":
                while not stopped.wait(.05):
                    progress(.1)
            # The engine receives expanded UDP/TCP primitives; inspect the captured
            # project's original applications to distinguish codec study variants.
            originals = project.get("scenarios", {}).get("Muharrir", {}).get("apps", [])
            if scenario.get("notes") == "browser-study-error" and any(app.get("codec") == "g729" for app in originals):
                raise RuntimeError("Browser fixture: codec variant failed")
            return original(project, scenario, progress, log)

        Path(os.environ.get("NETLAB_SCENARIO_FIXTURE", "/tmp/netlab-scenario-fixture.json")).write_text(json.dumps(project), encoding="utf-8")
        try:
            with patch.object(analytic, "run", side_effect=run):
                uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("NETLAB_SCENARIO_PORT", "8766")), log_level="warning")
        finally:
            stopped.set()
            app.state.runs.close()


if __name__ == "__main__":
    main()
