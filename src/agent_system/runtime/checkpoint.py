from enum import Enum
from typing import Union

from agent_system.runtime.state_store import CURRENT_SCHEMA_VERSION, StateManager


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

    def begin_task(self, task_index: int, task_id: str) -> dict:
        s = self.state.load()
        completed = s.get("delivery", {}).get("completed_task_index")
        delivery = {
            "current_task_index": task_index,
            "task_id": task_id,
            "active_task_id": task_id,
            "phase": TaskPhase.EXECUTING.value,
            "review_attempt": 1,
            "last_review_reason": "",
        }
        if isinstance(completed, int):
            delivery["completed_task_index"] = completed
        s["delivery"] = delivery
        if "schema_version" not in s:
            s["schema_version"] = CURRENT_SCHEMA_VERSION
        self.state.save(s)
        return s

    def set_current_task(self, task_index: int, task_id: str) -> dict:
        return self.begin_task(task_index, task_id)

    def set_phase(self, phase: Union[TaskPhase, str], **fields) -> dict:
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

    def enter_reviewing(self, review_snapshot: dict) -> dict:
        return self.set_phase(TaskPhase.REVIEWING, review_snapshot=review_snapshot)

    def enter_committing(self, pending_commit_message: str, pre_commit_sha=None) -> dict:
        return self.set_phase(TaskPhase.COMMITTING, pending_commit_message=pending_commit_message, pre_commit_sha=pre_commit_sha)

    def enter_pushing(self, commit_sha: str) -> dict:
        return self.set_phase(TaskPhase.PUSHING, commit_sha=commit_sha)

    def enter_ci_discovery(self, commit_sha: str) -> dict:
        return self.set_phase(TaskPhase.CI_DISCOVERY, commit_sha=commit_sha)

    def enter_waiting_ci(self, ci_runs=None, commit_sha=None) -> dict:
        fields = {}
        if ci_runs is not None:
            fields["ci_runs"] = ci_runs
        if commit_sha is not None:
            fields["commit_sha"] = commit_sha
        fields["ci_status"] = "WAITING"
        return self.set_phase(TaskPhase.WAITING_CI, **fields)

    def enter_ci_review(self, ci_status: str = "CI_FAILED", ci_failed_logs: str = "", ci_runs=None, commit_sha=None) -> dict:
        fields = {"ci_status": ci_status, "ci_failed_logs": ci_failed_logs}
        if ci_runs is not None:
            fields["ci_runs"] = ci_runs
        if commit_sha is not None:
            fields["commit_sha"] = commit_sha
        return self.set_phase(TaskPhase.CI_REVIEW, **fields)

    def mark_task_completed(self, task_index: int, task_id: str, outcome: str = "CHANGED", commit_sha=None, push_status=None, ci_status=None, ci_runs=None) -> dict:
        s = self.state.load()
        delivery = dict(s.get("delivery") or {})
        delivery.update({"current_task_index": task_index, "completed_task_index": task_index, "task_id": task_id, "active_task_id": task_id, "phase": TaskPhase.TASK_COMPLETED.value, "task_outcome": outcome})
        if commit_sha is not None:
            delivery["commit_sha"] = commit_sha
        if push_status is not None:
            delivery["push_status"] = push_status
        if ci_status is not None:
            delivery["ci_status"] = ci_status
        if ci_runs is not None:
            delivery["ci_runs"] = ci_runs
        delivery.pop("correction_task", None)
        delivery.pop("correction_attempt", None)
        delivery.pop("active_task_id", None)
        delivery.pop("review_snapshot", None)
        delivery.pop("pending_commit_message", None)
        delivery.pop("pre_commit_sha", None)
        delivery.pop("task_baseline", None)
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
        delivery["active_task_id"] = correction_task.get("id") if isinstance(correction_task, dict) else None
        delivery["phase"] = TaskPhase.CORRECTING.value
        for k in ("review_snapshot", "pending_commit_message", "pre_commit_sha", "push_status", "task_baseline"):
            delivery.pop(k, None)
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
        if phase is not None and phase not in {p.value for p in TaskPhase}:
            raise RuntimeError(f"invalid phase: {phase}")
        if phase in (TaskPhase.EXECUTING.value, TaskPhase.REVIEWING.value, TaskPhase.COMMITTING.value, TaskPhase.PUSHING.value, TaskPhase.CI_DISCOVERY.value, TaskPhase.WAITING_CI.value, TaskPhase.CI_REVIEW.value, TaskPhase.CORRECTING.value):
            if delivery.get("current_task_index") is None or not delivery.get("task_id"):
                raise RuntimeError(f"{phase} requires current_task_index and task_id")
        if phase == TaskPhase.REVIEWING.value and not delivery.get("review_snapshot"):
            raise RuntimeError("REVIEWING requires review_snapshot")
        if phase == TaskPhase.REVIEWING.value:
            snap = delivery.get("review_snapshot") or {}
            if "project_diff" not in snap:
                raise RuntimeError("REVIEWING review_snapshot requires project_diff")
        if phase == TaskPhase.COMMITTING.value and not delivery.get("pending_commit_message"):
            raise RuntimeError("COMMITTING requires pending_commit_message")
        if phase == TaskPhase.COMMITTING.value and not delivery.get("pre_commit_sha") and delivery.get("pre_commit_sha") is not None:
            pass
        if phase == TaskPhase.PUSHING.value and not delivery.get("commit_sha"):
            raise RuntimeError("PUSHING requires commit_sha")
        if phase == TaskPhase.CI_DISCOVERY.value and not delivery.get("commit_sha"):
            raise RuntimeError("CI_DISCOVERY requires commit_sha")
        if phase == TaskPhase.WAITING_CI.value and not delivery.get("commit_sha"):
            raise RuntimeError("WAITING_CI requires commit_sha")
        if phase == TaskPhase.CI_REVIEW.value and not delivery.get("commit_sha"):
            raise RuntimeError("CI_REVIEW requires commit_sha")
        if phase == TaskPhase.CI_REVIEW.value and delivery.get("ci_status") != "CI_FAILED":
            raise RuntimeError("CI_REVIEW requires ci_status == CI_FAILED")
        if phase == TaskPhase.CORRECTING.value and not delivery.get("correction_task"):
            raise RuntimeError("CORRECTING requires correction_task")
        if phase == TaskPhase.CORRECTING.value and not isinstance(delivery.get("correction_attempt"), int):
            raise RuntimeError("CORRECTING requires correction_attempt")
        if phase == TaskPhase.TASK_COMPLETED.value:
            cti = delivery.get("completed_task_index")
            cur = delivery.get("current_task_index")
            if isinstance(cti, int) and cti < 0:
                raise RuntimeError("TASK_COMPLETED with invalid completed_task_index")
            if isinstance(cti, int) and isinstance(cur, int) and cur != cti:
                raise RuntimeError("TASK_COMPLETED requires current_task_index == completed_task_index")
        if "schema_version" in s and s["schema_version"] != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(f"schema_version mismatch: {s['schema_version']} != {CURRENT_SCHEMA_VERSION}")
