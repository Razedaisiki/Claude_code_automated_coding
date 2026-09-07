from pathlib import Path

from agent_system.agents.models import AgentTask, FileSnapshot, TaskBaseline
from agent_system.runtime.git import Git


def capture_task_baseline(root: Path, task: AgentTask) -> TaskBaseline:
    root = Path(root).resolve() if root else Path.cwd().resolve()
    git = Git(root)
    files = {}
    for rel in (task.files or []):
        # path safety: reject escape
        if rel.startswith("/") or ".." in Path(rel).parts:
            files[rel] = FileSnapshot(exists=False)
            continue
        p = root / rel
        # check symlink parents
        try:
            if p.is_symlink():
                try:
                    target = str(p.readlink())
                except Exception:
                    target = "(symlink)"
                files[rel] = FileSnapshot(exists=True, content="", sha=f"symlink:{target}")
                continue
            # check parent symlink
            is_parent_link = False
            for parent in p.parents:
                try:
                    if parent == root:
                        break
                    if parent.is_symlink():
                        is_parent_link = True
                        break
                except Exception:
                    pass
            if is_parent_link:
                files[rel] = FileSnapshot(exists=False)
                continue
        except Exception:
            pass
        # containment check
        try:
            resolved = p.resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                files[rel] = FileSnapshot(exists=False)
                continue
        except Exception:
            files[rel] = FileSnapshot(exists=False)
            continue
        if p.is_file():
            try:
                data = p.read_bytes()
                sha = __import__("hashlib").sha256(data).hexdigest()
                files[rel] = FileSnapshot(exists=True, content="", sha=sha)
            except Exception:
                files[rel] = FileSnapshot(exists=True)
        elif p.is_symlink():
            try:
                target = str(p.readlink())
                files[rel] = FileSnapshot(exists=True, content="", sha=f"symlink:{target}")
            except Exception:
                files[rel] = FileSnapshot(exists=True)
        else:
            files[rel] = FileSnapshot(exists=False)
    r = git.shell.run(["git", "rev-parse", "HEAD"])
    sha = r.stdout.strip() if r.returncode == 0 else ""
    return TaskBaseline(commit_sha=sha, files=files)
