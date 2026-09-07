from pathlib import Path
from typing import List

from agent_system.runtime.path_safety import safe_read_text


class Filesystem:
    def __init__(self, root: Path = None):
        self.root = (root or Path.cwd()).resolve()

    def _resolve(self, path: str) -> Path:
        from agent_system.runtime.path_safety import check_containment, is_safe_relative_path
        if not is_safe_relative_path(path):
            raise ValueError(f"path escapes workspace: {path}")
        p = (self.root / path)
        # symlink parent check
        parts = Path(path).parts
        cur = self.root
        for part in parts:
            cur = cur / part
            try:
                if cur.is_symlink():
                    raise ValueError(f"symlink not allowed: {path} -> {cur.readlink()}")
            except ValueError:
                raise
            except Exception:
                pass
        # final containment
        ok, msg = check_containment(self.root, p)
        if not ok:
            raise ValueError(msg)
        # Symlink file itself
        if p.is_symlink():
            raise ValueError(f"symlink not allowed: {path}")
        return p.resolve()

    def read_file(self, path: str) -> str:
        text, err = safe_read_text(self.root, path)
        if err:
            raise ValueError(err)
        return text

    def write_file(self, path: str, content: str) -> None:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    RUNTIME_EXCLUDES = {".git", ".agent", ".pytest_cache", "__pycache__"}

    def list_files(self, pattern: str = "**/*") -> List[str]:
        files = []
        for p in self.root.glob(pattern):
            if not p.is_file():
                continue
            rel = p.relative_to(self.root)
            if any(part in self.RUNTIME_EXCLUDES for part in rel.parts):
                continue
            if rel.suffix == ".pyc":
                continue
            files.append(str(rel))
        return files

    def exists(self, path: str) -> bool:
        try:
            self._resolve(path)
            return (self.root / path).exists()
        except ValueError:
            return False
