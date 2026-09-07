from enum import Enum
from typing import Union

from agent_system.runtime.state_store import CURRENT_SCHEMA_VERSION, StateManager


class TaskPhase(str, Enum):
    EXECUTING = "EXECUTING"
    VALIDATING = "VALIDATING"
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
        self.state.save(s, event="task_started")
        return s

    def set_current_task(self, task_index: int, task_id: str) -> dict:
        return self.begin_task(task_index, task_id)

    def set_phase(self, phase: Union[TaskPhase, str], event: str = None, **fields) -> dict:
        s = self.state.load()
        delivery = dict(s.get("delivery") or {})
        val = phase.value if isinstance(phase, TaskPhase) else str(phase)
        delivery["phase"] = val
        for k, v in fields.items():
            delivery[k] = v
        s["delivery"] = delivery
        if "schema_version" not in s:
            s["schema_version"] = CURRENT_SCHEMA_VERSION
        ev = event or f"enter_{val.lower()}"
        self.state.save(s, event=ev)
        return s

    def enter_validating(self, **fields) -> dict:
        return self.set_phase(TaskPhase.VALIDATING, **fields)

    def enter_reviewing(self, review_snapshot: dict) -> dict:
        return self.set_phase(TaskPhase.REVIEWING, review_snapshot=review_snapshot)

    def enter_committing(self, pending_commit_message: str = None, pre_commit_sha=None, commit_intent: dict = None) -> dict:
        fields = {}
        if pending_commit_message is not None:
            fields["pending_commit_message"] = pending_commit_message
        if pre_commit_sha is not None:
            fields["pre_commit_sha"] = pre_commit_sha
        if commit_intent is not None:
            fields["commit_intent"] = commit_intent
        return self.set_phase(TaskPhase.COMMITTING, **fields)

    def enter_pushing(self, commit_sha: str = None, push_target: dict = None) -> dict:
        fields = {}
        if commit_sha is not None:
            fields["commit_sha"] = commit_sha
        if push_target is not None:
            fields["push_target"] = push_target
        return self.set_phase(TaskPhase.PUSHING, **fields)

    def enter_ci_discovery(self, commit_sha: str = None) -> dict:
        return self.set_phase(TaskPhase.CI_DISCOVERY, commit_sha=commit_sha)

    def enter_waiting_ci(self, ci_runs=None, commit_sha=None, frozen_ci_runs=None) -> dict:
        fields = {}
        if ci_runs is not None:
            fields["ci_runs"] = ci_runs
        if frozen_ci_runs is not None:
            fields["frozen_ci_runs"] = frozen_ci_runs
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
        delivery.pop("commit_intent", None)
        delivery.pop("task_baseline", None)
        delivery.pop("validation_snapshot", None)
        delivery.pop("push_target", None)
        delivery.pop("frozen_ci_runs", None)
        s["delivery"] = delivery
        if "schema_version" not in s:
            s["schema_version"] = CURRENT_SCHEMA_VERSION
        # task_history idempotent
        th = list(s.get("task_history") or [])
        found = False
        for e in th:
            if isinstance(e, dict) and e.get("task_index") == task_index and e.get("task_id") == task_id:
                found = True
                break
        if not found:
            entry = {"task_index": task_index, "task_id": task_id, "active_task_id": task_id, "outcome": outcome, "commit_sha": commit_sha, "push_status": push_status, "ci_status": ci_status}
            th.append(entry)
            s["task_history"] = th
        self.state.save(s, event="task_completed")
        return s

    def save_correction_task(self, correction_task, attempt: int = 1) -> dict:
        s = self.state.load()
        delivery = dict(s.get("delivery") or {})
        delivery["correction_task"] = correction_task
        delivery["correction_attempt"] = attempt
        delivery["active_task_id"] = correction_task.get("id") if isinstance(correction_task, dict) else None
        delivery["phase"] = TaskPhase.CORRECTING.value
        for k in ("review_snapshot", "pending_commit_message", "pre_commit_sha", "commit_intent", "push_status", "task_baseline", "validation_snapshot", "push_target", "frozen_ci_runs"):
            delivery.pop(k, None)
        s["delivery"] = delivery
        self.state.save(s, event="enter_correcting")
        return s

    def clear_correction_task(self) -> dict:
        s = self.state.load()
        delivery = dict(s.get("delivery") or {})
        delivery.pop("correction_task", None)
        delivery.pop("correction_attempt", None)
        s["delivery"] = delivery
        self.state.save(s, event="clear_correction")
        return s

    def update_delivery(self, event: str = None, **fields) -> dict:
        s = self.state.load()
        delivery = dict(s.get("delivery") or {})
        delivery.update(fields)
        s["delivery"] = delivery
        self.state.save(s, event=event or "update_delivery")
        return s

    def validate(self) -> None:
        s = self.state.load()
        _validate_state_dict(s)

def _validate_state_dict(s: dict) -> None:
    delivery = s.get("delivery") or {}
    phase = delivery.get("phase")
    if phase is not None and phase not in {p.value for p in TaskPhase}:
        raise RuntimeError(f"invalid phase: {phase}")
    if phase in (TaskPhase.EXECUTING.value, TaskPhase.VALIDATING.value, TaskPhase.REVIEWING.value, TaskPhase.COMMITTING.value, TaskPhase.PUSHING.value, TaskPhase.CI_DISCOVERY.value, TaskPhase.WAITING_CI.value, TaskPhase.CI_REVIEW.value, TaskPhase.CORRECTING.value):
        if delivery.get("current_task_index") is None or not delivery.get("task_id"):
            raise RuntimeError(f"{phase} requires current_task_index and task_id")
    if phase == TaskPhase.VALIDATING.value:
        if not delivery.get("reviewed_tree_sha") and not delivery.get("review_snapshot"):
            pass
        if delivery.get("reviewed_tree_sha") and not delivery.get("base_commit_sha"):
            raise RuntimeError("VALIDATING requires base_commit_sha when reviewed_tree_sha present")
    if phase == TaskPhase.REVIEWING.value and not delivery.get("review_snapshot"):
        raise RuntimeError("REVIEWING requires review_snapshot")
    if phase == TaskPhase.REVIEWING.value:
        snap = delivery.get("review_snapshot") or {}
        if "project_diff" not in snap:
            raise RuntimeError("REVIEWING review_snapshot requires project_diff")
        if "reviewed_tree_sha" not in snap:
            raise RuntimeError("REVIEWING review_snapshot requires reviewed_tree_sha")
        if "base_commit_sha" not in snap:
            raise RuntimeError("REVIEWING review_snapshot requires base_commit_sha")
    if phase == TaskPhase.COMMITTING.value:
        if not delivery.get("commit_intent") and not delivery.get("pending_commit_message"):
            raise RuntimeError("COMMITTING requires commit_intent or pending_commit_message")
        ci = delivery.get("commit_intent") or {}
        if ci:
            if not ci.get("tree_sha"):
                raise RuntimeError("COMMITTING commit_intent requires tree_sha")
            if not ci.get("head_ref"):
                raise RuntimeError("COMMITTING commit_intent requires head_ref")
    if phase == TaskPhase.PUSHING.value and not delivery.get("commit_sha"):
        raise RuntimeError("PUSHING requires commit_sha")
    if phase == TaskPhase.PUSHING.value and not delivery.get("push_target"):
        raise RuntimeError("PUSHING requires push_target")
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
        out = delivery.get("task_outcome")
        if out and out not in ("CHANGED", "SATISFIED", "VERIFIED", "SKIPPED"):
            raise RuntimeError(f"TASK_COMPLETED invalid outcome: {out}")
    if "schema_version" in s and s["schema_version"] != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(f"schema_version mismatch: {s['schema_version']} != {CURRENT_SCHEMA_VERSION}")
