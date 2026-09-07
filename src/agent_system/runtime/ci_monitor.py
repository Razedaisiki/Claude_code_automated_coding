import time
from pathlib import Path
from typing import List, Optional

from agent_system.runtime.github import GitHub


class CIMonitor:
    def __init__(self, root: Path = None, poll_interval: int = 10, timeout: int = 300, discovery_timeout: int = 60, settle_window: int = 15, discovery_poll_interval: int = 5):
        self.root = Path(root or Path.cwd()).resolve()
        self.github = GitHub(self.root)
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.discovery_timeout = discovery_timeout
        self.settle_window = settle_window
        self.discovery_poll_interval = discovery_poll_interval

    def discover_for_commit(self, commit_sha: str) -> dict:
        if not commit_sha:
            return {"status": "CI_NOT_DETECTED", "runs": [], "message": "no commit sha"}
        first = self.github.get_runs_for_commit(commit_sha)
        if first.get("status") == "ERROR":
            return {"status": "CI_DISCOVERY_ERROR", "runs": [], "message": first.get("message", "query error")}
        seen = {}
        last_new = None
        if first.get("runs"):
            for r in first["runs"]:
                rid = str(r.get("databaseId", ""))
                if rid:
                    seen[rid] = r
            last_new = time.time()
        start = time.time()
        while time.time() - start < self.discovery_timeout:
            # check settle
            if seen and last_new is not None and (time.time() - last_new) >= self.settle_window:
                return {"status": "CI_FOUND", "runs": list(seen.values()), "message": "runs discovered stable"}
            time.sleep(self.discovery_poll_interval)
            cur = self.github.get_runs_for_commit(commit_sha)
            if cur.get("status") == "ERROR":
                return {"status": "CI_DISCOVERY_ERROR", "runs": [], "message": cur.get("message", "query error")}
            runs = cur.get("runs") or []
            has_new = False
            for r in runs:
                rid = str(r.get("databaseId", ""))
                if rid and rid not in seen:
                    seen[rid] = r
                    has_new = True
            if has_new:
                last_new = time.time()
        if seen:
            return {"status": "CI_FOUND", "runs": list(seen.values()), "message": "runs discovered"}
        # Verify query success throughout
        final = self.github.get_runs_for_commit(commit_sha)
        if final.get("status") == "ERROR":
            return {"status": "CI_DISCOVERY_ERROR", "runs": [], "message": final.get("message", "query error")}
        if not (final.get("runs")) and not seen:
            return {"status": "CI_NOT_DETECTED", "runs": [], "message": "no runs detected for commit"}
        if seen:
            return {"status": "CI_FOUND", "runs": list(seen.values()), "message": "runs discovered"}
        return {"status": "CI_NOT_DETECTED", "runs": [], "message": "no runs detected for commit"}

    def wait_for_runs(self, runs: List[dict], poll_interval: int = None, timeout: int = None) -> dict:
        if not runs:
            return {"status": "CI_NOT_DETECTED", "runs": [], "message": "no runs"}
        poll = poll_interval or self.poll_interval
        tout = timeout or self.timeout
        deadline = time.time() + tout
        # Freeze run IDs; do not replace with discovered new runs during waiting
        frozen_ids = {str(r.get("databaseId", "")) for r in runs if r.get("databaseId") is not None}
        cur_runs = {str(r.get("databaseId", "")): dict(r) for r in runs if r.get("databaseId") is not None}
        # fallback for runs without id
        if not cur_runs:
            cur_runs = {str(i): dict(r) for i, r in enumerate(runs)}
            frozen_ids = set(cur_runs.keys())
        while time.time() < deadline:
            all_done = True
            failed = []
            for rid in list(frozen_ids):
                r = cur_runs.get(rid)
                if r is None:
                    continue
                st = self.github.get_run_status(rid) if rid.isdigit() else None
                if st:
                    r.update(st)
                    cur_runs[rid] = r
                if r.get("status") != "completed":
                    all_done = False
                if r.get("conclusion") in ("failure", "timed_out", "cancelled", "startup_failure"):
                    failed.append(r)
            if failed:
                logs = ""
                for fr in failed[:2]:
                    logs += self.github.get_failed_logs(str(fr.get("databaseId", "")))[:3000] + "\n"
                return {"status": "CI_FAILED", "runs": list(cur_runs.values()), "failed_logs": logs, "message": "ci failed"}
            if all_done:
                acceptable = {"success", "skipped", "neutral"}
                ok = all(r.get("status") == "completed" and r.get("conclusion") in acceptable for r in cur_runs.values())
                if ok:
                    return {"status": "CI_PASSED", "runs": list(cur_runs.values()), "message": "all passed"}
                return {"status": "CI_FAILED", "runs": list(cur_runs.values()), "message": f"unexpected conclusion: {next(iter(cur_runs.values())).get('conclusion') if cur_runs else 'unknown'}"}
            if time.time() + poll >= deadline:
                break
            time.sleep(poll)
        return {"status": "CI_FAILED", "runs": list(cur_runs.values()), "message": "timeout or incomplete"}

    def wait_for_commit(self, commit_sha: str, poll_interval: int = None, timeout: int = None) -> dict:
        if not commit_sha:
            return {"status": "CI_NOT_DETECTED", "runs": [], "message": "no commit sha"}
        disc = self.discover_for_commit(commit_sha)
        if disc["status"] == "CI_NOT_DETECTED":
            return disc
        if disc["status"] == "CI_DISCOVERY_ERROR":
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
