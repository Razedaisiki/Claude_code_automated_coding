import time
from pathlib import Path
from typing import Optional

from agent_system.runtime.github import GitHub


class CIMonitor:
    def __init__(self, root: Path = None, poll_interval: int = 10, timeout: int = 300):
        self.root = root or Path.cwd()
        self.github = GitHub(self.root)
        self.poll_interval = poll_interval
        self.timeout = timeout

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
