import json
from pathlib import Path


ALLOWED_STATUSES = {
    "INITIALIZED", "RUNNING", "REVIEW_PENDING", "COMMITTING",
    "PUSHING", "WAITING_CI", "CI_PASSED", "CI_FAILED", "COMPLETED", "FAILED",
}


class StateManager:
    def __init__(self, root: Path = None):
        self.root = root or Path.cwd()
        self.state_file = self.root / ".agent" / "state.json"

    def load(self) -> dict:
        if not self.state_file.exists():
            return {"status": "INITIALIZED", "session_id": None}
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def save(self, state: dict):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def update(self, **kwargs) -> dict:
        state = self.load()
        state.update(kwargs)
        self.save(state)
        return state
