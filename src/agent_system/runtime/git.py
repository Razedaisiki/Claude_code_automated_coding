from pathlib import Path

from agent_system.runtime.shell import Shell


class Git:
    def __init__(self, root: Path = None):
        self.root = root or Path.cwd()
        self.shell = Shell(self.root)

    def diff(self, args: str = "") -> str:
        r = self.shell.run(f"git diff {args}".strip())
        return r.stdout

    def diff_stat(self) -> str:
        r = self.shell.run("git diff --stat")
        return r.stdout

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
