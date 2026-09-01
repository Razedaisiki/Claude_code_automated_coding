from dataclasses import dataclass
from pathlib import Path
from typing import List

from agent_system.runtime.shell import Shell


@dataclass(frozen=True)
class ProjectChanges:
    diff: str
    changed_files: List[str]
    fingerprint: str
    has_changes: bool


class Git:
    def __init__(self, root: Path = None):
        self.root = (root or Path.cwd()).resolve()
        self.repo_root = self.root
        self.shell = Shell(self.root)

    def ensure_runtime_excludes(self):
        git_dir = self.root / ".git"
        if not git_dir.exists() or not git_dir.is_dir():
            return
        exclude = git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        needed = [".agent/", "__pycache__/", "*.pyc"]
        to_add = [l for l in needed if l not in existing]
        if to_add:
            with exclude.open("a", encoding="utf-8") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                for l in to_add:
                    f.write(l + "\n")

    def is_workspace_repo(self) -> bool:
        return self._check_workspace_repo()

    def ensure_runtime_isolation(self):
        if not self._check_workspace_repo():
            return False
        self.ensure_runtime_excludes()
        r = self.shell.run("git ls-files -- .agent")
        if r.returncode != 0:
            return False
        tracked = r.stdout.strip()
        if not tracked:
            return True
        print("Agent runtime files were tracked by Git. Removing .agent from the repository index.")
        self.shell.run("git rm -r --cached --ignore-unmatch .agent")
        r2 = self.shell.run("git ls-files -- .agent")
        if r2.returncode == 0 and r2.stdout.strip():
            print(f"  Warning: still tracked: {r2.stdout.strip()[:80]}")
        return True

    def _check_workspace_repo(self) -> bool:
        r = self.shell.run("git rev-parse --show-toplevel")
        if r.returncode != 0:
            return False
        found = Path(r.stdout.strip()).resolve()
        return found == self.root

    def _guard(self) -> bool:
        if not self._check_workspace_repo():
            return False
        return True

    def head_sha(self) -> str:
        if not self._guard():
            return ""
        r = self.shell.run("git rev-parse HEAD")
        return r.stdout.strip() if r.returncode == 0 else ""

    def commit_parent(self, sha: str = "HEAD") -> str:
        if not sha:
            return ""
        if not self._guard():
            return ""
        r = self.shell.run(f"git rev-parse {sha}^")
        return r.stdout.strip() if r.returncode == 0 else ""

    def commit_subject(self, sha: str = "HEAD") -> str:
        if not sha:
            return ""
        if not self._guard():
            return ""
        r = self.shell.run(f"git log -1 --format=%s {sha}")
        return r.stdout.strip() if r.returncode == 0 else ""

    def diff(self, args: str = "") -> str:
        if not self._guard():
            return ""
        if args:
            base = f"git diff {args}".strip()
            r = self.shell.run(base)
            return r.stdout
        return self.project_changes()

    def project_changes(self) -> str:
        if not self._guard():
            return ""
        r = self.shell.run("git diff HEAD")
        out = r.stdout
        u = self.shell.run("git ls-files --others --exclude-standard")
        if u.stdout.strip():
            for f in u.stdout.strip().splitlines():
                f = f.strip()
                if f:
                    p = self.root / f
                    if p.is_file():
                        try:
                            content = p.read_text(encoding="utf-8")
                            out += f"\nnew file: {f}\n{content[:3000]}\n"
                        except Exception:
                            out += f"\nnew file: {f}\n"
                    else:
                        out += f"\nnew file: {f}\n"
        return out

    def diff_stat(self, args: str = "") -> str:
        if not self._guard():
            return ""
        base = f"git diff --stat {args}".strip() if args else "git diff --stat"
        r = self.shell.run(base)
        out = r.stdout
        ls_args = args.strip() if args.strip() else ""
        u = self.shell.run(f"git ls-files --others --exclude-standard -- {ls_args}".strip() if ls_args else "git ls-files --others --exclude-standard")
        if u.stdout.strip():
            out += "\n[untracked] " + u.stdout.strip()
        return out

    def status(self) -> str:
        if not self._guard():
            return ""
        r = self.shell.run("git status --porcelain -uall")
        return r.stdout

    def project_diff(self) -> str:
        raw = self.diff()
        if not raw:
            return ""
        lines = []
        skip = False
        for l in raw.splitlines():
            if l.startswith("diff --git") and ".agent/" in l:
                skip = True
                continue
            if l.startswith("diff --git"):
                skip = False
            if skip:
                continue
            if ".agent/" in l or "__pycache__" in l or ".pyc" in l:
                continue
            lines.append(l)
        return "\n".join(lines)

    def commit(self, message: str) -> dict:
        if not message or not message.strip():
            return {"status": "FAILED", "returncode": 1, "sha": None, "message": "empty message"}
        if not self._guard():
            return {"status": "FAILED", "returncode": 1, "sha": None, "message": "not a git repository"}
        before = self.head_sha() or None
        self.ensure_runtime_isolation()
        self.shell.run("git add -A")
        r = self.shell.run(f"git commit -m {self._quote(message)}")
        after = self.head_sha() or None
        out = (r.stdout + r.stderr).strip()
        if r.returncode == 0 and after and after != before:
            return {"status": "SUCCESS", "returncode": 0, "sha": after, "message": out}
        if r.returncode == 0 and after == before:
            return {"status": "FAILED", "returncode": 1, "sha": after, "message": "commit did not advance HEAD: " + out}
        return {"status": "FAILED", "returncode": r.returncode, "sha": after, "message": out}

    def commit_diff(self, sha: str) -> str:
        if not sha:
            return ""
        if not self._guard():
            return ""
        r = self.shell.run(f"git show --format= --find-renames {sha}")
        if r.stdout.strip():
            return r.stdout + r.stderr
        r2 = self.shell.run(f"git show {sha} | head -200")
        return r2.stdout + r2.stderr

    def has_commits(self) -> bool:
        if not self._guard():
            return False
        r = self.shell.run("git rev-parse HEAD")
        return r.returncode == 0

    def has_remote(self) -> bool:
        if not self._guard():
            return False
        r = self.shell.run("git remote")
        return bool(r.stdout.strip())

    def remote_url(self) -> str:
        if not self._guard():
            return ""
        r = self.shell.run("git remote get-url origin")
        return r.stdout.strip() if r.returncode == 0 else ""

    def push(self) -> dict:
        if not self.has_remote():
            return {"status": "NO_REMOTE", "message": "no remote configured"}
        r = self.shell.run("git push")
        out = r.stdout + r.stderr
        if r.returncode == 0:
            return {"status": "SUCCESS", "message": out.strip()}
        return {"status": "REMOTE_FAILED", "message": out.strip()}

    def _quote(self, s: str) -> str:
        return "'" + s.replace("'", "'\"'\"'") + "'"

    def changed_files(self) -> list:
        if not self._guard():
            return []
        tracked = self.shell.run("git diff --name-only HEAD").stdout
        untracked = self.shell.run("git ls-files --others --exclude-standard").stdout
        files = set()
        for line in tracked.splitlines():
            f = line.strip()
            if f and ".agent/" not in f and "__pycache__" not in f and not f.endswith(".pyc"):
                files.add(f)
        for line in untracked.splitlines():
            f = line.strip()
            if f and ".agent/" not in f and "__pycache__" not in f and not f.endswith(".pyc"):
                files.add(f)
        return sorted(files)

    def project_changes_model(self) -> ProjectChanges:
        import hashlib

        raw = self.project_changes()
        files = self.changed_files()
        return ProjectChanges(diff=raw, changed_files=files, fingerprint=hashlib.sha256(raw.encode()).hexdigest(), has_changes=bool(raw.strip()))
