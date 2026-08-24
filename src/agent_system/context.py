from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentContext:
    task: str
    claude_md: str
    milestone: str
    plan: str
    repo_info: str


def load_context(root: Path = None) -> AgentContext:
    root = root or Path.cwd()

    def read(p: Path) -> str:
        return p.read_text(encoding="utf-8") if p.exists() else ""

    task = read(root / "TASK.md")
    claude_md = read(root / "CLAUDE.md")
    plan = read(root / ".agent" / "plan.md")

    milestones_dir = root / ".agent" / "milestones"
    milestone = ""
    if milestones_dir.exists():
        parts = []
        for f in sorted(milestones_dir.iterdir()):
            if f.is_file():
                parts.append(f"## {f.name}\n{read(f)}")
        milestone = "\n\n".join(parts)

    repo_info = ""
    git_head = root / ".git" / "HEAD"
    if git_head.exists():
        repo_info = read(git_head).strip()

    return AgentContext(
        task=task,
        claude_md=claude_md,
        milestone=milestone,
        plan=plan,
        repo_info=repo_info,
    )
