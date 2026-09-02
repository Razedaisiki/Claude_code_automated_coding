import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agent_system.runtime.git import Git


@dataclass(frozen=True)
class GitControlState:
    head_sha: str
    branch: str
    staged_fingerprint: str
    upstream_sha: str


def _staged_fingerprint(git: Git) -> str:
    r = git.shell.run("git diff --cached --binary")
    return hashlib.sha256(r.stdout.encode("utf-8")).hexdigest()


def capture_git_control_state(root: Path = None) -> GitControlState:
    git = Git(root)
    head = git.head_sha() or ""
    br = ""
    if git.is_workspace_repo():
        r = git.shell.run("git branch --show-current")
        if r.returncode == 0:
            br = r.stdout.strip()
    staged = _staged_fingerprint(git) if git.is_workspace_repo() else hashlib.sha256(b"").hexdigest()
    upstream = ""
    if git.is_workspace_repo():
        r2 = git.shell.run("git rev-parse @{u} 2>&1")
        if r2.returncode == 0:
            upstream = r2.stdout.strip().splitlines()[0].strip() if r2.stdout.strip() else ""
    return GitControlState(head_sha=head, branch=br, staged_fingerprint=staged, upstream_sha=upstream)


def validate_unchanged(before: GitControlState, after: GitControlState) -> Optional[str]:
    violations = []
    if before.head_sha != after.head_sha:
        violations.append("HEAD changed")
    if before.branch != after.branch:
        violations.append("branch changed")
    if before.staged_fingerprint != after.staged_fingerprint:
        violations.append("Git index changed")
    if before.upstream_sha != after.upstream_sha:
        violations.append("upstream tracking ref changed")
    if violations:
        return "CodeAgent violated Runtime Git authority: " + ", ".join(violations)
    return None
