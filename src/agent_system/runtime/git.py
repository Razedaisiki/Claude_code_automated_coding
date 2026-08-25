from pathlib import Path

from agent_system.runtime.shell import Shell


class Git:
    def __init__(self, root: Path = None):
        self.root = root or Path.cwd()
        self.shell = Shell(self.root)

    def diff(self, args: str = "") -> str:
        base = f"git diff {args}".strip() if args else "git diff"
        r = self.shell.run(base)
        out = r.stdout
        if args:
            u = self.shell.run(f"git diff --no-index -- /dev/null {args} 2>/dev/null; git ls-files --others --exclude-standard -- {args} 2>/dev/null")
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
                                pass
        else:
            u = self.shell.run("git ls-files --others --exclude-standard")
            if u.stdout.strip():
                out += "\n[untracked]\n" + u.stdout
        return out

    def diff_stat(self, args: str = "") -> str:
        cmd = f"git diff --stat {args}".strip() if args else "git diff --stat"
        r = self.shell.run(cmd)
        out = r.stdout
        if args:
            u = self.shell.run(f"git ls-files --others --exclude-standard -- {args}")
            if u.stdout.strip():
                out += "\n[untracked] " + u.stdout.strip()
        return out

    def status(self) -> str:
        r = self.shell.run("git status --porcelain")
        return r.stdout

    def changed_files(self) -> list:
        out = self.status()
        files = []
        for line in out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                files.append(parts[-1])
        return files
