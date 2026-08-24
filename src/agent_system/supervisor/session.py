import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class SessionManager:
    def __init__(self, root: Path = None):
        self.root = root or Path.cwd()
        self.runtime_dir = self.root / ".agent" / "runtime"

    def create(self) -> dict:
        sid = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
        now = datetime.now(timezone.utc).isoformat()
        task = ""
        task_file = self.root / "TASK.md"
        if task_file.exists():
            task = task_file.read_text(encoding="utf-8")
        session = {"id": sid, "started": now, "task": task}
        session_dir = self.runtime_dir / sid
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "context.json").write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")
        return session

    def get(self, session_id: str) -> Optional[dict]:
        p = self.runtime_dir / session_id / "context.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def latest(self) -> Optional[dict]:
        if not self.runtime_dir.exists():
            return None
        sessions = sorted(self.runtime_dir.iterdir())
        if not sessions:
            return None
        return self.get(sessions[-1].name)
