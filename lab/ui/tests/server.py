"""Isolated browser-review server. Never reads or modifies saved user results.

Run from the repository root: python -m lab.ui.tests.server
"""

import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch

import uvicorn

from lab.benchmarks.report_charts import fixture
from lab.engines import analytic
from lab.server import create_app
from lab.topogen import generate


def seed(app):
    project = generate("ring", routers=3, hosts=1, flows=0)
    project["name"] = "ui_review"
    edge = next(key for key, link in project["links"].items()
                if "h0x0" in (link["a"]["dev"], link["b"]["dev"]))
    project["scenarios"] = {"Sifat sinovi": {"duration": 12,
        "failures": {"seed": 3, "distribution": "exponential", "links": {"mtbf_s": 30, "mttr_s": 2, "targets": [edge]}}, "events": [
        {"t": 5, "action": "link_down", "link": edge},
        {"t": 7, "action": "link_up", "link": edge}], "apps": [
        {"id": "voice", "type": "voip", "codec": "g711", "src": "h0x0", "dst": "h2x0", "start": 1, "stop": 12},
        {"id": "web", "type": "http", "page": "500KB", "think": 2, "src": "h0x0", "dst": "h2x0", "start": 1, "stop": 12},
        {"id": "file", "type": "ftp", "file": "1MB", "src": "h0x0", "dst": "h2x0", "start": 1, "stop": 12}]}}
    queue = app.state.runs

    def wait(ids):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if all(queue.get(project["name"], rid)["status"] in ("done", "error") for rid in ids):
                for rid in ids:
                    record = queue.get(project["name"], rid)
                    assert record["status"] == "done", record
                return
            time.sleep(.02)
        raise TimeoutError(ids)

    rich = queue.start(project, "Sifat sinovi", "analytic", extra={"label": "Ovoz va veb sifati"})["run_id"]
    second = queue.start(project, "Sifat sinovi", "analytic", extra={"label": "Taqqoslash namunasi"})["run_id"]
    wait([rich, second])
    plan = queue.start_study(project, "Sifat sinovi", "analytic", {"kind": "link", "link": edge, "field": "bw", "label": "Kabel"}, [.2, 1, 2])
    wait(plan["run_ids"])
    # An explicit failed fixture exercises partial studies, rather than hiding failed variants.
    failed = queue.get(project["name"], plan["run_ids"][1])
    failed.update(status="error", error="Browser fixture: variant failed", flows={})
    queue._save(project["name"], failed)
    two = queue.start_study(project, "Sifat sinovi", "analytic",
        {"kind": "link", "link": edge, "field": "bw"}, [.2, 2],
        {"kind": "link", "link": edge, "field": "delay"}, [1, 5])
    wait(two["run_ids"])
    large = fixture(100000)
    large.update(run_id="large", project=project["name"], label="100 000 nuqtali signal", created=time.time()-1)
    queue._save(project["name"], large)
    for i in range(51):
        queue._save(project["name"], {"run_id": f"archive_{i}", "project": project["name"], "label": f"Arxiv {i}",
            "scenario_name": "Arxiv", "engine": "analytic", "status": "done", "created": i+1, "duration": 1, "flows": {}})
    from lab.engines import manager
    Path(manager.RESULTS_DIR, project["name"], "broken.json").write_text("{broken", encoding="utf-8")

    original = analytic.run
    stopped = threading.Event()

    def held(project, scenario, *args, **kwargs):
        if scenario.get("notes") == "browser-held":
            progress = kwargs.get("progress") or (args[0] if args else None)
            while not stopped.wait(.05):
                if progress:
                    progress(.1)
        return original(project, scenario, *args, **kwargs)

    # Restore at server shutdown; cancellation is the real queue API path.
    app.state.browser_patch = patch.object(analytic, "run", side_effect=held)
    app.state.browser_patch.start()
    hold = copy.deepcopy(project)
    hold["scenarios"]["Sifat sinovi"].update(notes="browser-held", apps=[])
    cancel = queue.start(hold, "Sifat sinovi", "analytic", extra={"label": "Bekor qilinadigan sinov"})["run_id"]
    app.state.browser_stop = stopped
    return {"project": project, "rich": rich, "second": second, "study": plan["study_id"],
            "study2": two["study_id"], "study2runs": two["run_ids"], "cancel": cancel, "total": 62}


def main():
    with tempfile.TemporaryDirectory(prefix="netlab-ui-review-") as directory, \
            patch("lab.engines.manager.RESULTS_DIR", directory), \
            patch("lab.server.PROJECTS_DIR", str(Path(directory, "projects"))):
        app = create_app("demo")
        try:
            manifest = seed(app)
            Path(os.environ.get("NETLAB_UI_FIXTURE", "/tmp/netlab-ui-fixture.json")).write_text(json.dumps(manifest), encoding="utf-8")
            uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("NETLAB_UI_PORT", "8765")), log_level="warning")
        finally:
            if hasattr(app.state, "browser_stop"):
                app.state.browser_stop.set()
                app.state.browser_patch.stop()
            app.state.runs.close()


if __name__ == "__main__":
    main()
