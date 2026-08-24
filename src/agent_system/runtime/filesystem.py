from pathlib import Path
from typing import List


class Filesystem:
    def __init__(self, root: Path = None):
        self.root = (root or Path.cwd()).resolve()

    def _resolve(self, path: str) -> Path:
        p = (self.root / path).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError(f"path escapes workspace: {path}")
        return p

    def read_file(self, path: str) -> str:
        return self._resolve(path).read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> None:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def list_files(self, pattern: str = "**/*") -> List[str]:
        return [str(p.relative_to(self.root)) for p in self.root.glob(pattern) if p.is_file()]

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()
