from pathlib import Path

from agent_system.runtime.shell import Shell


class Git:
    def __init__(self, root: Path = None):
        self.root = (root or Path.cwd()).resolve()
        self.repo_root = self.root
        self.shell = Shell(self.root)

    def diff(self, args: str = "") -> str:
        base = f"git diff {args}".strip() if args else "git diff"
        r = self.shell.run(base)
        out = r.stdout
        ls_args = args.strip() if args.strip() else ""
        u = self.shell.run(f"git ls-files --others --exclude-standard -- {ls_args}".strip() if ls_args else "git ls-files --others --exclude-standard")
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
        return out or r.stdout

    def diff_stat(self, args: str = "") -> str:
        base = f"git diff --stat {args}".strip() if args else "git diff --stat"
        r = self.shell.run(base)
        out = r.stdout
        ls_args = args.strip() if args.strip() else ""
        u = self.shell.run(f"git ls-files --others --exclude-standard -- {ls_args}".strip() if ls_args else "git ls-files --others --exclude-standard")
        if u.stdout.strip():
            out += "\n[untracked] " + u.stdout.strip()
        return out

    def status(self) -> str:
        r = self.shell.run("git status --porcelain")
        return r.stdout

    def commit(self, message: str) -> str:
        if not message or not message.strip():
            return "empty message"
        self.shell.run("git add -A")
        r = self.shell.run(f"git commit -m {self._quote(message)}")
        return r.stdout + r.stderr

    def has_commits(self) -> bool:
        r = self.shell.run("git rev-parse HEAD 2>&1")
        return r.returncode == 0

    def has_remote(self) -> bool:
        r = self.shell.run("git remote 2>&1")
        return bool(r.stdout.strip())

    def remote_url(self) -> str:
        r = self.shell.run("git remote get-url origin 2>&1")
        return r.stdout.strip() if r.returncode == 0 else ""

    def push(self, remote: str = "origin", branch: str = None) -> dict:
        if not self.has_remote():
            return {"status": "NO_REMOTE", "message": "no remote configured"}
        if branch is None:
            r = self.shell.run("git rev-parse --abbrev-ref HEAD")
            branch = r.stdout.strip() or "main"
        r = self.shell.run(f"git push {remote} {branch} 2>&1")
        out = r.stdout + r.stderr
        if r.returncode == 0:
            return {"status": "SUCCESS", "message": out.strip()}
        return {"status": "REMOTE_FAILED", "message": out.strip()}

    def _quote(self, s: str) -> str:
        return "'" + s.replace("'", "'\"'\"'") + "'"

    def changed_files(self) -> list:
        out = self.status()
        files = []
        for line in out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                files.append(parts[-1])
        return files
