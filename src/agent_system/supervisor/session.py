import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent_system.runtime.atomic_io import atomic_write_json, atomic_write_text


class SessionManager:
    def __init__(self, root: Path = None):
        self.root = Path(root or Path.cwd()).resolve()
        self.runtime_dir = self.root / ".agent" / "runtime"

    def _sha256(self, text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

    def create(self, task_sha256: str = "", start_head_sha: str = "") -> dict:
        sid = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
        now = datetime.now(timezone.utc).isoformat()
        task = ""
        task_file = self.root / "TASK.md"
        if task_file.exists():
            task = task_file.read_text(encoding="utf-8")
        sha = task_sha256 or self._sha256(task)
        session = {"id": sid, "started": now, "task": task, "task_sha256": sha, "start_head_sha": start_head_sha or ""}
        session_dir = self.runtime_dir / sid
        session_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(session_dir / "context.json", session)
        return session

    def update_start_head(self, session_id: str, start_head_sha: str):
        p = self.runtime_dir / session_id / "context.json"
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data["start_head_sha"] = start_head_sha or ""
            atomic_write_json(p, data)
        except Exception:
            pass

    def freeze_plan(self, session_id: str, plan_data, plan_text: str, plan_sha256: str = ""):
        session_dir = self.runtime_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        pj = session_dir / "plan.json"
        pm = session_dir / "plan.md"
        if pj.exists():
            try:
                existing = json.loads(pj.read_text(encoding="utf-8"))
                existing_sha = hashlib.sha256(json.dumps(existing, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
                new_sha = plan_sha256 or hashlib.sha256(json.dumps(plan_data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest() if plan_data else hashlib.sha256((plan_text or "").encode("utf-8")).hexdigest()
                if existing_sha != new_sha:
                    raise RuntimeError(f"Frozen plan for session {session_id} already exists with different content; refusing to overwrite.")
                return
            except RuntimeError:
                raise
            except Exception:
                pass
        if plan_data is not None:
            atomic_write_json(pj, plan_data)
        else:
            try:
                if pj.exists():
                    pass
            except Exception:
                pass
        if plan_text is not None:
            atomic_write_text(pm, plan_text or "")

    def get(self, session_id: str) -> Optional[dict]:
        p = self.runtime_dir / session_id / "context.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def get_plan(self, session_id: str):
        pj = self.runtime_dir / session_id / "plan.json"
        pm = self.runtime_dir / session_id / "plan.md"
        data = None
        text = ""
        if pj.exists():
            try:
                data = json.loads(pj.read_text(encoding="utf-8"))
            except Exception:
                data = None
        if pm.exists():
            try:
                text = pm.read_text(encoding="utf-8")
            except Exception:
                text = ""
        return data, text

    def write_result(self, session_id: str, result: dict):
        session_dir = self.runtime_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(session_dir / "result.json", result)

    def latest(self) -> Optional[dict]:
        if not self.runtime_dir.exists():
            return None
        sessions = sorted(self.runtime_dir.iterdir())
        if not sessions:
            return None
        return self.get(sessions[-1].name)
