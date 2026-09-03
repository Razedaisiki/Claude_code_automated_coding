from pathlib import Path

from agent_system.agents.models import AgentTask, FileSnapshot, TaskBaseline
from agent_system.runtime.git import Git


def capture_task_baseline(root: Path, task: AgentTask) -> TaskBaseline:
    root = Path(root).resolve() if root else Path.cwd().resolve()
    git = Git(root)
    files = {}
    for rel in (task.files or []):
        p = root / rel
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8")
                files[rel] = FileSnapshot(exists=True, content=content[:3000])
            except Exception:
                files[rel] = FileSnapshot(exists=True)
        else:
            files[rel] = FileSnapshot(exists=False)
    r = git.shell.run("git rev-parse HEAD")
    sha = r.stdout.strip() if r.returncode == 0 else ""
    return TaskBaseline(commit_sha=sha, files=files)
