from enum import Enum

from agent_system.supervisor.state import StateManager


CURRENT_SCHEMA_VERSION = 2


class TaskPhase(str, Enum):
    EXECUTING = "EXECUTING"
    REVIEWING = "REVIEWING"
    COMMITTING = "COMMITTING"
    PUSHING = "PUSHING"
    CI_DISCOVERY = "CI_DISCOVERY"
    WAITING_CI = "WAITING_CI"
    CI_REVIEW = "CI_REVIEW"
    CORRECTING = "CORRECTING"
    TASK_COMPLETED = "TASK_COMPLETED"


class Checkpoint:
    def __init__(self, root=None):
        self.state = StateManager(root)

    def set_current_task(self, task_index: int, task_id: str) -> dict:
        s = self.state.load()
        delivery = dict(s.get("delivery") or {})
        delivery.update({"current_task_index": task_index, "task_id": task_id, "phase": TaskPhase.EXECUTING.value})
        s["delivery"] = delivery
        if "schema_version" not in s:
            s["schema_version"] = CURRENT_SCHEMA_VERSION
        self.state.save(s)
        return s

    def set_phase(self, phase: "TaskPhase | str", **fields) -> dict:  # type: ignore[syntax]
        s = self.state.load()
        delivery = dict(s.get("delivery") or {})
        val = phase.value if isinstance(phase, TaskPhase) else str(phase)
        delivery["phase"] = val
        for k, v in fields.items():
            delivery[k] = v
        s["delivery"] = delivery
        if "schema_version" not in s:
            s["schema_version"] = CURRENT_SCHEMA_VERSION
        self.state.save(s)
        return s

    def mark_task_completed(self, task_index: int, task_id: str, outcome: str = "CHANGED", commit_sha=None, push_status=None, ci_status=None) -> dict:
        s = self.state.load()
        delivery = dict(s.get("delivery") or {})
        delivery.update({"current_task_index": task_index, "completed_task_index": task_index, "task_id": task_id, "phase": TaskPhase.TASK_COMPLETED.value, "task_outcome": outcome})
        if commit_sha is not None:
            delivery["commit_sha"] = commit_sha
        if push_status is not None:
            delivery["push_status"] = push_status
        if ci_status is not None:
            delivery["ci_status"] = ci_status
        delivery.pop("correction_task", None)
        delivery.pop("correction_attempt", None)
        s["delivery"] = delivery
        if "schema_version" not in s:
            s["schema_version"] = CURRENT_SCHEMA_VERSION
        self.state.save(s)
        return s

    def save_correction_task(self, correction_task, attempt: int = 1) -> dict:
        s = self.state.load()
        delivery = dict(s.get("delivery") or {})
        delivery["correction_task"] = correction_task
        delivery["correction_attempt"] = attempt
        delivery["phase"] = TaskPhase.CORRECTING.value
        s["delivery"] = delivery
        self.state.save(s)
        return s

    def clear_correction_task(self) -> dict:
        s = self.state.load()
        delivery = dict(s.get("delivery") or {})
        delivery.pop("correction_task", None)
        delivery.pop("correction_attempt", None)
        s["delivery"] = delivery
        self.state.save(s)
        return s

    def update_delivery(self, **fields) -> dict:
        s = self.state.load()
        delivery = dict(s.get("delivery") or {})
        delivery.update(fields)
        s["delivery"] = delivery
        self.state.save(s)
        return s

    def validate(self) -> None:
        s = self.state.load()
        delivery = s.get("delivery") or {}
        phase = delivery.get("phase")
        if phase == TaskPhase.WAITING_CI.value and not delivery.get("commit_sha"):
            raise RuntimeError("WAITING_CI requires commit_sha")
        if phase == TaskPhase.CORRECTING.value and not delivery.get("correction_task"):
            raise RuntimeError("CORRECTING requires correction_task")
        if phase == TaskPhase.TASK_COMPLETED.value:
            cti = delivery.get("completed_task_index")
            cur = delivery.get("current_task_index")
            if isinstance(cti, int) and isinstance(cur, int) and cti < 0:
                raise RuntimeError("TASK_COMPLETED with invalid completed_task_index")
            if isinstance(cti, int) and isinstance(cur, int) and cur < cti:
                raise RuntimeError("current_task_index < completed_task_index")
        if "schema_version" in s and s["schema_version"] != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(f"schema_version mismatch: {s['schema_version']} != {CURRENT_SCHEMA_VERSION}")
