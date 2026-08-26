from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from agent_system.delivery import DeliveryConfig
from agent_system.runtime.ci_monitor import CIMonitor
from agent_system.runtime.git import Git


@dataclass
class DeliveryResult:
    mode: str
    commit_sha: Optional[str] = None
    push_status: str = "SKIPPED"
    push_message: str = ""
    ci_status: str = "NOT_CONFIGURED"
    ci_runs: List[dict] = field(default_factory=list)
    failed_logs: str = ""


class DeliveryRuntime:
    def __init__(self, root: Path = None):
        self.root = root or Path.cwd()
        self.git = Git(self.root)
        self.config = DeliveryConfig.load(self.root)

    def commit(self, message: str) -> str:
        return self.git.commit(message)

    def deliver(self, commit_sha: str = None) -> DeliveryResult:
        if commit_sha is None:
            r = self.git.shell.run("git rev-parse HEAD 2>&1")
            commit_sha = r.stdout.strip() if r.returncode == 0 else None

        if self.config.mode != "gh":
            return DeliveryResult(mode="local", commit_sha=commit_sha, push_status="SKIPPED", ci_status="CI_NOT_DETECTED")

        push_res = self.git.push()
        if push_res["status"] == "NO_REMOTE":
            return DeliveryResult(mode="gh", commit_sha=commit_sha, push_status="NO_REMOTE", push_message=push_res["message"], ci_status="CI_NOT_DETECTED")
        if push_res["status"] == "REMOTE_FAILED":
            return DeliveryResult(mode="gh", commit_sha=commit_sha, push_status="REMOTE_FAILED", push_message=push_res["message"], ci_status="CI_NOT_DETECTED")

        ci_res = CIMonitor(self.root).wait_for_commit(commit_sha)
        ci_status = ci_res.get("status", "CI_NOT_DETECTED")
        runs = ci_res.get("runs", [])
        logs = ci_res.get("failed_logs", "")
        return DeliveryResult(mode="gh", commit_sha=commit_sha, push_status="SUCCESS", push_message=push_res["message"], ci_status=ci_status, ci_runs=runs, failed_logs=logs)
