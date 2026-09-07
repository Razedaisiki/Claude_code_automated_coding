import hashlib
import json
from pathlib import Path
from typing import Optional

from agent_system.runtime.atomic_io import atomic_write_bytes


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class ReviewArtifactStore:
    def __init__(self, root: Path, session_id: str):
        self.root = Path(root).resolve()
        self.session_id = session_id
        self.base_dir = self.root / ".agent" / "runtime" / session_id / "review"

    def _artifact_dir(self, task_id: str, attempt: int) -> Path:
        return self.base_dir / task_id / f"attempt-{attempt}"

    def _write(self, path: Path, obj) -> dict:
        data = _canonical(obj)
        sha = _sha256(data)
        if path.exists():
            existing = path.read_bytes()
            # Compare canonical sha of existing content (stored with newline)
            try:
                existing_obj = json.loads(existing.decode("utf-8"))
                existing_canonical = _canonical(existing_obj)
                if _sha256(existing_canonical) == sha:
                    return {"path": str(path.relative_to(self.root)), "sha256": sha}
            except Exception:
                pass
            if _sha256(existing.rstrip(b"\n")) == sha or _sha256(existing) == sha:
                return {"path": str(path.relative_to(self.root)), "sha256": sha}
            raise RuntimeError(f"Artifact already exists with different content: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, data + b"\n")
        return {"path": str(path.relative_to(self.root)), "sha256": sha}

    def write_candidate(self, task_id: str, attempt: int, obj) -> dict:
        return self._write(self._artifact_dir(task_id, attempt) / "candidate.json", obj)

    def write_validation(self, task_id: str, attempt: int, obj) -> dict:
        return self._write(self._artifact_dir(task_id, attempt) / "validation.json", obj)

    def write_review_package(self, task_id: str, attempt: int, obj) -> dict:
        return self._write(self._artifact_dir(task_id, attempt) / "review-package.json", obj)

    def _load(self, ref: dict) -> dict:
        if not isinstance(ref, dict) or "path" not in ref or "sha256" not in ref:
            raise RuntimeError(f"Invalid artifact ref: {ref}")
        p = self.root / ref["path"]
        if not p.exists():
            raise RuntimeError(f"Artifact missing: {p}")
        raw = p.read_bytes()
        try:
            obj = json.loads(raw.decode("utf-8"))
            canonical = _canonical(obj)
            if _sha256(canonical) == ref["sha256"] or _sha256(raw) == ref["sha256"] or _sha256(raw.rstrip(b"\n")) == ref["sha256"]:
                return obj
            raise RuntimeError(f"Artifact sha mismatch: {p}")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Artifact invalid JSON: {p}: {e}") from e

    def load_candidate(self, ref: dict) -> dict:
        return self._load(ref)

    def load_validation(self, ref: dict) -> dict:
        return self._load(ref)

    def load_review_package(self, ref: dict) -> dict:
        return self._load(ref)
