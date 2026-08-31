import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class Milestone:
    name: str
    content: str


@dataclass
class ProjectContext:
    task: str
    instructions: str
    milestones: List[Milestone]
    plan: str
    repository: str
    plan_data: dict = field(default=None)

    @property
    def claude_md(self) -> str:
        return self.instructions

    @property
    def milestone(self) -> str:
        return "\n\n".join(f"## {m.name}\n{m.content}" for m in self.milestones)

    @property
    def repo_info(self) -> str:
        return self.repository


def load_context(root: Path = None) -> ProjectContext:
    root = root or Path.cwd()

    def read(p: Path) -> str:
        return p.read_text(encoding="utf-8") if p.exists() else ""

    task = read(root / "TASK.md")
    instructions = read(root / "CLAUDE.md")
    plan = read(root / ".agent" / "plan.md")
    plan_data = None
    jpath = root / ".agent" / "plan.json"
    if jpath.exists():
        try:
            plan_data = json.loads(jpath.read_text(encoding="utf-8"))
            if plan_data and not plan:
                from agent_system.plan_parser import render_plan_md

                plan = render_plan_md(plan_data)
        except Exception:
            plan_data = None

    milestones: List[Milestone] = []
    milestones_dir = root / ".agent" / "milestones"
    if milestones_dir.exists():
        for f in sorted(milestones_dir.iterdir()):
            if f.is_file():
                milestones.append(Milestone(name=f.name, content=read(f)))

    repository = ""
    git_head = root / ".git" / "HEAD"
    if git_head.exists():
        repository = read(git_head).strip()

    return ProjectContext(
        task=task,
        instructions=instructions,
        milestones=milestones,
        plan=plan,
        repository=repository,
        plan_data=plan_data,
    )
