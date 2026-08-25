import subprocess
from pathlib import Path

from agent_system.runtime.shell import Shell


class Git:
    def __init__(self, root: Path = None):
        self.root = root or Path.cwd()
        self.shell = Shell(self.root)
        try:
            out = subprocess.run("git rev-parse --show-toplevel", shell=True, capture_output=True, text=True, timeout=5)
            self.repo_root = Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else self.root
        except Exception:
            self.repo_root = self.root

    def _rel(self, p: str) -> str:
        try:
            abs_p = (self.root / p).resolve()
            return str(abs_p.relative_to(self.repo_root))
        except Exception:
            return p

    def diff(self, args: str = "") -> str:
        rel_args = " ".join(self._rel(a) for a in args.split()) if args else ""
        base = f"git diff {rel_args}".strip() if rel_args else "git diff"
        r = Shell(self.repo_root).run(base)
        out = r.stdout
        ls_args = rel_args if rel_args else ""
        u = Shell(self.repo_root).run(f"git ls-files --others --exclude-standard -- {ls_args}".strip() if ls_args else "git ls-files --others --exclude-standard")
        if u.stdout.strip():
            for f in u.stdout.strip().splitlines():
                f = f.strip()
                if f:
                    p = self.repo_root / f
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
        rel_args = " ".join(self._rel(a) for a in args.split()) if args else ""
        base = f"git diff --stat {rel_args}".strip() if rel_args else "git diff --stat"
        r = Shell(self.repo_root).run(base)
        out = r.stdout
        ls_args = rel_args if rel_args else ""
        u = Shell(self.repo_root).run(f"git ls-files --others --exclude-standard -- {ls_args}".strip() if ls_args else "git ls-files --others --exclude-standard")
        if u.stdout.strip():
            out += "\n[untracked] " + u.stdout.strip()
        return out

    def status(self) -> str:
        r = Shell(self.repo_root).run("git status --porcelain")
        return r.stdout

    def changed_files(self) -> list:
        out = self.status()
        files = []
        for line in out.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                files.append(parts[-1])
        return files
