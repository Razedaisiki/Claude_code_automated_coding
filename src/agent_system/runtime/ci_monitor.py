import time
from pathlib import Path
from typing import List, Optional

from agent_system.runtime.github import GitHub


class CIMonitor:
    def __init__(self, root: Path = None, poll_interval: int = 10, timeout: int = 300):
        self.root = root or Path.cwd()
        self.github = GitHub(self.root)
        self.poll_interval = poll_interval
        self.timeout = timeout

    def wait_for_commit(self, commit_sha: str, poll_interval: int = None, timeout: int = None) -> dict:
        if not commit_sha:
            return {"status": "CI_NOT_CONFIGURED", "runs": [], "message": "no commit sha"}

        poll = poll_interval or self.poll_interval
        tout = timeout or self.timeout

        discovery_window = 30
        start = time.time()
        runs: List[dict] = []
        while time.time() - start < discovery_window:
            runs = self.github.get_runs_for_commit(commit_sha)
            if runs:
                break
            time.sleep(5)

        if not runs:
            return {"status": "CI_NOT_CONFIGURED", "runs": [], "message": "no runs for commit"}

        deadline = time.time() + tout
        while time.time() < deadline:
            all_done = True
            failed = []
            for r in runs:
                rid = str(r.get("databaseId", ""))
                st = self.github.get_run_status(rid)
                if st:
                    r.update(st)
                if r.get("status") != "completed":
                    all_done = False
                if r.get("conclusion") in ("failure", "timed_out", "cancelled", "startup_failure"):
                    failed.append(r)

            if failed:
                logs = ""
                for fr in failed[:2]:
                    logs += self.github.get_failed_logs(str(fr.get("databaseId", "")))[:3000] + "\n"
                return {"status": "CI_FAILED", "runs": runs, "failed_logs": logs, "message": "ci failed"}

            if all_done:
                ok = all(r.get("conclusion") in ("success", "skipped", "neutral", "completed", None) or r.get("status") == "completed" for r in runs)
                if ok:
                    return {"status": "CI_PASSED", "runs": runs, "message": "all passed"}

            if time.time() + poll >= deadline:
                break
            time.sleep(poll)
            runs = self.github.get_runs_for_commit(commit_sha) or runs

        return {"status": "CI_FAILED", "runs": runs, "message": "timeout or incomplete"}

    def wait_for_completion(self, run_id: str = None) -> Optional[dict]:
        if run_id is None:
            latest = self.github.get_latest_run()
            if not latest:
                return None
            run_id = str(latest.get("databaseId", ""))
        start = time.time()
        while time.time() - start < self.timeout:
            status = self.github.get_run_status(run_id)
            if status and status.get("status") == "completed":
                return status
            if status and status.get("conclusion") in ("success", "failure", "cancelled", "timed_out"):
                return status
            time.sleep(self.poll_interval)
        return None
