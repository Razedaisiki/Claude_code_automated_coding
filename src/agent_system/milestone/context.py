from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class MilestoneContext:
    task: str
    plan: str
    diff: str
    git_log: str
    previous_milestones: List[str]
    human_feedback: Optional[str] = None
    repo_state: str = ""


def collect_context(root: Path = None, feedback: str = None) -> MilestoneContext:
    from agent_system.context import load_context
    from agent_system.runtime.git import Git
    from agent_system.runtime.shell import Shell

    root = root or Path.cwd()
    ctx = load_context(root)
    git = Git(root)
    shell = Shell(root)

    diff = git.diff()
    git_log = shell.run("git log --oneline -10 2>&1").stdout

    milestones_dir = root / ".agent" / "milestones"
    previous: List[str] = []
    if milestones_dir.exists():
        for f in sorted(milestones_dir.glob("*.md")):
            try:
                previous.append(f.read_text(encoding="utf-8"))
            except Exception:
                pass

    repo_state = ""
    try:
        repo_state = shell.run("git status --porcelain 2>&1").stdout
        repo_state += "\n" + shell.run("ls -la src/ 2>&1 | head -20").stdout
    except Exception:
        pass

    fb_file = root / ".agent" / "human_feedback.md"
    if not feedback and fb_file.exists():
        try:
            feedback = fb_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    return MilestoneContext(
        task=ctx.task,
        plan=ctx.plan,
        diff=diff,
        git_log=git_log,
        previous_milestones=previous,
        human_feedback=feedback,
        repo_state=repo_state,
    )
