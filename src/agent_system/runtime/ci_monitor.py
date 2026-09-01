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

    def discover_for_commit(self, commit_sha: str) -> dict:
        if not commit_sha:
            return {"status": "CI_NOT_DETECTED", "runs": [], "message": "no commit sha"}
        runs = self.github.get_runs_for_commit(commit_sha)
        if runs:
            return {"status": "CI_FOUND", "runs": runs, "message": "runs discovered"}
        start = time.time()
        while time.time() - start < 60:
            time.sleep(5)
            runs = self.github.get_runs_for_commit(commit_sha)
            if runs:
                return {"status": "CI_FOUND", "runs": runs, "message": "runs discovered"}
        return {"status": "CI_NOT_DETECTED", "runs": [], "message": "no runs detected for commit"}

    def wait_for_runs(self, runs: List[dict], poll_interval: int = None, timeout: int = None) -> dict:
        if not runs:
            return {"status": "CI_NOT_DETECTED", "runs": [], "message": "no runs"}
        poll = poll_interval or self.poll_interval
        tout = timeout or self.timeout
        deadline = time.time() + tout
        cur_runs = list(runs)
        while time.time() < deadline:
            all_done = True
            failed = []
            for r in cur_runs:
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
                return {"status": "CI_FAILED", "runs": cur_runs, "failed_logs": logs, "message": "ci failed"}

            if all_done:
                acceptable = {"success", "skipped", "neutral"}
                ok = all(r.get("status") == "completed" and r.get("conclusion") in acceptable for r in cur_runs)
                if ok:
                    return {"status": "CI_PASSED", "runs": cur_runs, "message": "all passed"}
                return {"status": "CI_FAILED", "runs": cur_runs, "message": f"unexpected conclusion: {cur_runs[0].get('conclusion') if cur_runs else 'unknown'}"}

            if time.time() + poll >= deadline:
                break
            time.sleep(poll)
            refreshed = self.github.get_runs_for_commit(cur_runs[0].get("headSha", "") or "") if cur_runs and cur_runs[0].get("headSha") else None
            if refreshed:
                cur_runs = refreshed

        return {"status": "CI_FAILED", "runs": cur_runs, "message": "timeout or incomplete"}

    def wait_for_commit(self, commit_sha: str, poll_interval: int = None, timeout: int = None) -> dict:
        if not commit_sha:
            return {"status": "CI_NOT_DETECTED", "runs": [], "message": "no commit sha"}
        disc = self.discover_for_commit(commit_sha)
        if disc["status"] == "CI_NOT_DETECTED":
            return disc
        return self.wait_for_runs(disc["runs"], poll_interval=poll_interval, timeout=timeout)

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
