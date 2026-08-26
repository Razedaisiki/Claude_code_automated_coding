from pathlib import Path
from typing import Optional

from agent_system.runtime.shell import Shell


class GitHub:
    def __init__(self, root: Path = None):
        self.root = root or Path.cwd()
        self.shell = Shell(self.root)

    def _has_gh(self) -> bool:
        r = self.shell.run("which gh 2>&1")
        return r.returncode == 0

    def get_runs_for_commit(self, commit_sha: str) -> list:
        if not self._has_gh() or not commit_sha:
            return []
        r = self.shell.run(f"gh run list --commit {commit_sha} --json databaseId,status,conclusion,workflowName,headSha 2>&1")
        if r.returncode != 0 or not r.stdout.strip():
            return []
        try:
            import json

            data = json.loads(r.stdout)
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []

    def get_latest_run(self, branch: str = None) -> Optional[dict]:
        if not self._has_gh():
            return None
        cmd = "gh run list --limit 1 --json databaseId,status,conclusion,headBranch,workflowName 2>&1"
        r = self.shell.run(cmd)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        try:
            import json

            data = json.loads(r.stdout)
            if isinstance(data, list) and data:
                return data[0]
        except Exception:
            pass
        return None

    def get_run_status(self, run_id: str) -> Optional[dict]:
        if not self._has_gh():
            return None
        r = self.shell.run(f"gh run view {run_id} --json status,conclusion 2>&1")
        if r.returncode != 0:
            return None
        try:
            import json

            return json.loads(r.stdout)
        except Exception:
            return None

    def get_run_logs(self, run_id: str) -> str:
        if not self._has_gh():
            return ""
        r = self.shell.run(f"gh run view {run_id} --log 2>&1 | head -100")
        return r.stdout[:5000] if r.stdout else ""

    def get_failed_logs(self, run_id: str) -> str:
        if not self._has_gh():
            return ""
        r = self.shell.run(f"gh run view {run_id} --log-failed 2>&1 | head -200")
        return r.stdout[:5000] if r.stdout else self.get_run_logs(run_id)
