import json
from pathlib import Path

CURRENT_SCHEMA_VERSION = 3

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
            return {"status": "INITIALIZED", "session_id": None, "schema_version": CURRENT_SCHEMA_VERSION}
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        if "schema_version" not in data:
            data["schema_version"] = 1
        return data

    def save(self, state: dict):
        if "schema_version" not in state:
            state["schema_version"] = CURRENT_SCHEMA_VERSION
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def start_new_execution(self, session_id: str) -> dict:
        state = self.load()
        state["schema_version"] = CURRENT_SCHEMA_VERSION
        state["status"] = "RUNNING"
        state["session_id"] = session_id
        state["execution_mode"] = "NEW"
        state["delivery"] = {}
        self.save(state)
        return state

    def replace_delivery(self, delivery: dict) -> dict:
        state = self.load()
        state["delivery"] = dict(delivery)
        self.save(state)
        return state

    def update(self, **kwargs) -> dict:
        state = self.load()
        if "delivery" in kwargs and isinstance(kwargs["delivery"], dict) and isinstance(state.get("delivery"), dict):
            merged = dict(state["delivery"])
            merged.update(kwargs["delivery"])
            kwargs["delivery"] = merged
        state.update(kwargs)
        self.save(state)
        return state

    def update_delivery(self, **fields) -> dict:
        state = self.load()
        delivery = dict(state.get("delivery") or {})
        delivery.update(fields)
        state["delivery"] = delivery
        self.save(state)
        return state

    def validate(self) -> None:
        from agent_system.runtime.checkpoint import Checkpoint

        Checkpoint(self.root).validate()
