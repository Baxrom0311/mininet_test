"""Shell protocol for isolated remote runs; all user paths are shell-quoted."""

import re
import shlex


class RemoteJob:
    def __init__(self, run_id):
        if not re.fullmatch(r"run_[a-f0-9]{32}", run_id):
            raise ValueError("invalid remote run ID")
        self.root = f"/tmp/netlab-studio/{run_id}"
        self.data = self.root + "/data"
        self.log = self.root + "/run.log"
        self.status = self.root + "/exit-code"
        self.run_id = run_id

    def launch(self, repo, topology, routing, duration, seed):
        argv = ["timeout", "--foreground", "-k", "15", str(duration + 400), "env",
                f"DATA_DIR={self.data}", f"SIM_RUN_ID={self.run_id}", "python3", "-u", "light_simulation.py",
                "--topology", topology, "--routing", routing, "--duration", str(duration), "--seed", str(seed)]
        runner = (f"cd {shlex.quote(repo)}; rc=$?; if [ $rc -eq 0 ]; then {shlex.join(argv)}; rc=$?; fi; "
                  f"printf '%s\\n' \"$rc\" > {self.status}.tmp; mv {self.status}.tmp {self.status}; exit \"$rc\"")
        return (f"mkdir -p {self.root}; setsid bash -c {shlex.quote(runner)} "
                f"> {self.log} 2>&1 < /dev/null & echo STUDIO_PID=$!")

    def poll(self, pid):
        return (f"tail -n 40 {self.log} 2>/dev/null; "
                f"printf '\\nSTUDIO_STATUS='; if [ -f {self.status} ]; then cat {self.status}; else echo pending; fi; "
                f"if kill -0 -- -{int(pid)} 2>/dev/null; then echo STUDIO_RUNNING=1; else echo STUDIO_RUNNING=0; fi; "
                f"if [ -f {self.data}/datasets/metadata.json ]; then echo STUDIO_DATASET=1; else echo STUDIO_DATASET=0; fi")

    @staticmethod
    def stop(pid):
        if int(pid) <= 1:
            raise ValueError("invalid process group")
        return f"kill -TERM -- -{int(pid)} 2>/dev/null || true"

    @staticmethod
    def parse(text):
        values = {}
        logs = []
        for line in text.splitlines():
            match = re.fullmatch(r"STUDIO_(STATUS|RUNNING|DATASET)=(pending|-?\d+)", line)
            if match:
                values[match[1]] = match[2]
            else:
                logs.append(line)
        code = values.get("STATUS")
        return {"exit_code": int(code) if code not in (None, "pending") else None,
                "running": values.get("RUNNING") == "1", "dataset": values.get("DATASET") == "1",
                "log": "\n".join(logs)}
