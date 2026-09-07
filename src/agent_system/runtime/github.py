import json
from pathlib import Path
from typing import Optional

from agent_system.runtime.shell import Shell


class GitHub:
    def __init__(self, root: Path = None):
        self.root = Path(root or Path.cwd()).resolve()
        self.shell = Shell(self.root)

    def _has_gh(self) -> bool:
        r = self.shell.run(["which", "gh"])
        if r.returncode == 0:
            return True
        r2 = self.shell.run("which gh 2>&1")
        return r2.returncode == 0

    def _query(self, cmd):
        if isinstance(cmd, list):
            r = self.shell.run(cmd)
        else:
            r = self.shell.run(cmd)
        return r

    def get_runs_for_commit(self, commit_sha: str) -> dict:
        if not commit_sha:
            return {"status": "SUCCESS", "runs": [], "message": "no commit sha"}
        if not self._has_gh():
            return {"status": "ERROR", "runs": [], "message": "gh CLI not found"}
        r = self.shell.run(["gh", "run", "list", "--commit", commit_sha, "--json", "databaseId,status,conclusion,workflowName,headSha"])
        if r.returncode != 0:
            out = (r.stdout + r.stderr).strip()
            low = out.lower()
            if "not logged" in low or "auth" in low or "could not" in low:
                return {"status": "ERROR", "runs": [], "message": out[:500] or "gh auth failure"}
            return {"status": "ERROR", "runs": [], "message": out[:500] or "gh command failed"}
        if not r.stdout.strip():
            return {"status": "SUCCESS", "runs": [], "message": "no runs"}
        try:
            data = json.loads(r.stdout)
            if isinstance(data, list):
                return {"status": "SUCCESS", "runs": data, "message": "ok"}
        except Exception as e:
            return {"status": "ERROR", "runs": [], "message": f"parse error: {e}"}
        return {"status": "SUCCESS", "runs": [], "message": "no runs"}

    def get_latest_run(self, branch: str = None) -> Optional[dict]:
        if not self._has_gh():
            return None
        cmd = ["gh", "run", "list", "--limit", "1", "--json", "databaseId,status,conclusion,headBranch,workflowName"]
        r = self.shell.run(cmd)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        try:
            data = json.loads(r.stdout)
            if isinstance(data, list) and data:
                return data[0]
        except Exception:
            pass
        return None

    def get_run_status(self, run_id: str) -> Optional[dict]:
        if not self._has_gh():
            return None
        r = self.shell.run(["gh", "run", "view", str(run_id), "--json", "status,conclusion"])
        if r.returncode != 0:
            return None
        try:
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
