from __future__ import annotations

from pathlib import Path

from agent_system.agents.models import AgentResult, AgentTask
from agent_system.runtime.git import Git

MAX_REVIEW_ATTEMPTS = 3
MAX_CI_CORRECTIONS = 3


class TaskRuntime:
    def __init__(self, root: Path, *, coding_backend, tech_lead):
        self.root = Path(root).resolve() if root else Path.cwd().resolve()
        self.coding_backend = coding_backend
        self.tech_lead = tech_lead
        self.git = Git(self.root)

    def _resolve_active_task(self, original: AgentTask, delivery: dict) -> AgentTask:
        ct = delivery.get("correction_task")
        if isinstance(ct, dict) and ct.get("description"):
            return AgentTask(
                id=ct.get("id") or original.id,
                role=ct.get("role") or original.role,
                description=ct.get("description") or original.description,
                files=ct.get("files") or original.files,
                type=ct.get("type") or original.type,
                required=original.required,
                acceptance=ct.get("acceptance") or original.acceptance,
                validation=ct.get("validation") or original.validation,
            )
        return original

    def run_task(self, original: AgentTask, task_index: int, resume_current: bool = False) -> AgentResult:
        from agent_system.agents.code_agent import CodeAgent
        from agent_system.delivery import DeliveryConfig
        from agent_system.runtime.checkpoint import Checkpoint, TaskPhase
        from agent_system.runtime.ci_monitor import CIMonitor
        from agent_system.runtime.delivery_runtime import DeliveryRuntime

        ckpt = Checkpoint(self.root)
        if not resume_current:
            ckpt.begin_task(task_index, original.id)

        while True:
            delivery = (ckpt.state.load().get("delivery") or {})
            phase = delivery.get("phase") or TaskPhase.EXECUTING.value
            active = self._resolve_active_task(original, delivery)

            if phase == TaskPhase.TASK_COMPLETED.value:
                return AgentResult(status="SUCCESS", message=f"task {original.id} completed", artifacts=[])

            if phase == TaskPhase.EXECUTING.value:
                from agent_system.agents.models import task_baseline_from_dict, task_baseline_to_dict

                baseline = task_baseline_from_dict(delivery.get("task_baseline"))
                if baseline is None:
                    from agent_system.runtime.task_baseline import capture_task_baseline
                    baseline = capture_task_baseline(self.root, active)
                    ckpt.update_delivery(task_baseline=task_baseline_to_dict(baseline))
                review_attempt = int(delivery.get("review_attempt", 1))
                last_reason = delivery.get("last_review_reason", "")
                if active.role == "code" and review_attempt > 1 and last_reason:
                    exec_task = AgentTask(id=active.id, role=active.role, description=active.description + f"\n[Retry {review_attempt}: previous review failed: {last_reason}]", files=active.files, type=active.type, required=active.required, acceptance=active.acceptance, validation=active.validation)
                else:
                    exec_task = active
                if exec_task.role == "code":
                    from agent_system.runtime.git_control import capture_git_control_state, validate_unchanged
                    git_before = capture_git_control_state(self.root)
                    try:
                        result = CodeAgent(backend=self.coding_backend, root=self.root).execute(exec_task, baseline=baseline)
                    except Exception:
                        git_after = capture_git_control_state(self.root)
                        violation = validate_unchanged(git_before, git_after)
                        if violation:
                            return AgentResult(status="FAILED", message=violation, artifacts=[], execution_status="ERROR", stop_reason="runtime_authority_violation")
                        raise
                    git_after = capture_git_control_state(self.root)
                    violation = validate_unchanged(git_before, git_after)
                    if violation:
                        return AgentResult(status="FAILED", message=violation, artifacts=[], baseline=getattr(result, "baseline", None), evidence=getattr(result, "evidence", None), execution_status="ERROR", stop_reason="runtime_authority_violation")
                else:
                    return AgentResult(status="FAILED", message=f"unsupported task role: {exec_task.role}", artifacts=[])
                if getattr(result, "execution_status", "COMPLETED") == "ERROR" or result.status in ("FAILED", "INCOMPLETE"):
                    return AgentResult(status="FAILED", message=result.message, artifacts=result.artifacts)
                from agent_system.agents.models import execution_evidence_to_dict
                changes = self.git.project_changes_model()
                ckpt.enter_reviewing(review_snapshot={"result_status": result.status, "result_message": result.message, "result_artifacts": list(result.artifacts or []), "commit_message": getattr(result, "commit_message", "") or "", "outcome_status": getattr(getattr(result, "outcome", None), "status", "") if getattr(result, "outcome", None) else "", "execution_status": getattr(result, "execution_status", "COMPLETED") or "COMPLETED", "stop_reason": getattr(result, "stop_reason", None), "evidence": execution_evidence_to_dict(getattr(result, "evidence", None)), "project_diff": changes.diff, "changed_files": list(changes.changed_files or []), "project_fingerprint": changes.fingerprint, "active_task_id": exec_task.id})
                continue

            if phase == TaskPhase.REVIEWING.value:
                from agent_system.agents.models import execution_evidence_from_dict, task_baseline_from_dict
                snap = delivery.get("review_snapshot") or {}
                commit_message = snap.get("commit_message", "")
                outcome_status = snap.get("outcome_status", "")
                baseline = task_baseline_from_dict(delivery.get("task_baseline"))
                evidence = execution_evidence_from_dict(snap.get("evidence"))
                tmp_result = AgentResult(status=snap.get("result_status", "SUCCESS"), message=snap.get("result_message", ""), artifacts=snap.get("result_artifacts", []), baseline=baseline, evidence=evidence, execution_status=snap.get("execution_status", "COMPLETED") or "COMPLETED", stop_reason=snap.get("stop_reason"))
                if outcome_status:
                    from agent_system.agents.models import TaskOutcome
                    tmp_result.outcome = TaskOutcome(task_id=active.id, status=outcome_status)
                project_diff = snap.get("project_diff", "")
                review = self.tech_lead.review(active, tmp_result, project_diff)
                if commit_message and not getattr(review, "commit_message", ""):
                    review.commit_message = commit_message
                if review.status == "SUCCESS":
                    if getattr(review, "outcome", None) and review.outcome.status == "SATISFIED":
                        changes_now = self.git.project_changes_model()
                        if changes_now.has_changes:
                            return AgentResult(status="FAILED", message=f"Runtime invariant violation: SATISFIED task has pending project changes: {changes_now.changed_files}", artifacts=changes_now.changed_files)
                        ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="SATISFIED", commit_sha=None, push_status="SKIPPED", ci_status="SKIPPED")
                        return AgentResult(status="SUCCESS", message=review.message, artifacts=review.artifacts)
                    if not review.commit_message:
                        return AgentResult(status="FAILED", message="Approved changed task has no commit message", artifacts=tmp_result.artifacts)
                    ckpt.enter_committing(pending_commit_message=review.commit_message, pre_commit_sha=self.git.head_sha())
                    continue
                attempt = int(delivery.get("review_attempt", 1))
                if attempt >= MAX_REVIEW_ATTEMPTS:
                    return review
                ckpt.set_phase(TaskPhase.EXECUTING, review_attempt=attempt + 1, last_review_reason=review.message, review_snapshot=None)
                continue

            if phase == TaskPhase.COMMITTING.value:
                pending = delivery.get("pending_commit_message", "")
                pre_sha = delivery.get("pre_commit_sha")
                cur_sha = self.git.head_sha()
                if cur_sha and pre_sha and cur_sha != pre_sha:
                    parent = self.git.commit_parent(cur_sha)
                    subj = self.git.commit_subject(cur_sha)
                    if parent == (pre_sha or "") and subj == (pending or ""):
                        ckpt.enter_pushing(commit_sha=cur_sha)
                        continue
                    if cur_sha != pre_sha:
                        return AgentResult(status="FAILED", message="COMMITTING checkpoint inconsistent: HEAD advanced unexpectedly", artifacts=[])
                if not self.git.is_workspace_repo():
                    return AgentResult(status="FAILED", message="workspace not a Git repository", artifacts=[])
                changes = self.git.project_changes_model()
                if not changes.has_changes:
                    return AgentResult(status="FAILED", message="COMMITTING checkpoint inconsistent: approved changed task has no pending project changes", artifacts=[])
                cres = DeliveryRuntime(self.root).commit(pending)
                if cres.get("status") != "SUCCESS":
                    return AgentResult(status="FAILED", message=f"commit failed for {original.id}: {cres.get('message','')[:200]}", artifacts=[])
                sha = cres.get("sha")
                cfg = DeliveryConfig.load(self.root)
                if cfg.mode == "local":
                    ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status="SKIPPED", ci_status="SKIPPED")
                    return AgentResult(status="SUCCESS", message=f"committed {sha[:7]}", artifacts=[])
                ckpt.enter_pushing(commit_sha=sha)
                continue

            if phase == TaskPhase.PUSHING.value:
                sha = delivery.get("commit_sha", "")
                if not sha:
                    return AgentResult(status="FAILED", message="PUSHING requires commit_sha", artifacts=[])
                push_res = DeliveryRuntime(self.root).push(commit_sha=sha)
                if push_res["status"] == "SUCCESS":
                    ckpt.enter_ci_discovery(commit_sha=sha)
                    continue
                if push_res["status"] in ("REMOTE_FAILED", "NO_REMOTE"):
                    ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status=push_res["status"], ci_status="SKIPPED")
                    return AgentResult(status="SUCCESS", message=f"push {push_res['status']}", artifacts=[])
                return AgentResult(status="FAILED", message=f"push failed: {push_res.get('message','')[:200]}", artifacts=[])

            if phase == TaskPhase.CI_DISCOVERY.value:
                sha = delivery.get("commit_sha", "")
                disc = CIMonitor(self.root).discover_for_commit(sha)
                if disc["status"] == "CI_NOT_DETECTED":
                    ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status="SUCCESS", ci_status="CI_NOT_DETECTED")
                    return AgentResult(status="SUCCESS", message="CI not detected", artifacts=[])
                ckpt.enter_waiting_ci(ci_runs=disc.get("runs", []), commit_sha=sha)
                continue

            if phase == TaskPhase.WAITING_CI.value:
                sha = delivery.get("commit_sha", "")
                runs = delivery.get("ci_runs") or []
                if not runs and sha:
                    disc = CIMonitor(self.root).discover_for_commit(sha)
                    runs = disc.get("runs", [])
                    if not runs:
                        ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status="SUCCESS", ci_status="CI_NOT_DETECTED")
                        return AgentResult(status="SUCCESS", message="CI not detected", artifacts=[])
                    ckpt.set_phase(TaskPhase.WAITING_CI, ci_runs=runs)
                ci_res = CIMonitor(self.root).wait_for_runs(runs)
                if ci_res["status"] == "CI_PASSED":
                    ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status="SUCCESS", ci_status="CI_PASSED", ci_runs=ci_res.get("runs", runs))
                    return AgentResult(status="SUCCESS", message="CI passed", artifacts=[])
                if ci_res["status"] == "CI_NOT_DETECTED":
                    ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status="SUCCESS", ci_status="CI_NOT_DETECTED")
                    return AgentResult(status="SUCCESS", message="CI not detected", artifacts=[])
                ckpt.enter_ci_review(ci_status="CI_FAILED", ci_failed_logs=ci_res.get("failed_logs", ""), ci_runs=ci_res.get("runs", runs), commit_sha=sha)
                # persist final failed snapshot for resume
                ckpt.update_delivery(ci_runs=ci_res.get("runs", runs))
                continue

            if phase == TaskPhase.CI_REVIEW.value:
                ci_logs = delivery.get("ci_failed_logs", "")
                sha = delivery.get("commit_sha", "")
                decision = self.tech_lead.ci_review(ci_status="CI_FAILED", ci_logs=ci_logs, task=active, commit_sha=sha)
                if decision.get("decision") in ("APPROVED", "APPROVED_WITH_NOTE", "APPROVED_WITH_NOTE"):
                    ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status="SUCCESS", ci_status=decision.get("decision"))
                    return AgentResult(status="SUCCESS", message=decision.get("reason", "CI approved"), artifacts=[])
                if decision.get("decision") != "CHANGES_REQUIRED":
                    return AgentResult(status="FAILED", message=f"CI review invalid decision: {decision.get('decision')}", artifacts=[])
                corr = decision.get("correction")
                if not isinstance(corr, dict) or not corr.get("description"):
                    return AgentResult(status="FAILED", message="CI review CHANGES_REQUIRED without structured correction", artifacts=[])
                current = int(delivery.get("correction_attempt", 0) or 0)
                if current >= MAX_CI_CORRECTIONS:
                    return AgentResult(status="FAILED", message=f"CI correction limit exceeded for {original.id}", artifacts=[])
                next_attempt = current + 1
                corr_task = {
                    "id": f"{original.id}-correction-{next_attempt}",
                    "role": corr.get("role") or active.role,
                    "type": corr.get("type") or active.type,
                    "description": str(corr.get("description", "")).strip(),
                    "acceptance": list(corr.get("acceptance", [])) or list(active.acceptance or []),
                    "validation": list(corr.get("validation", [])) or list(active.validation or []),
                    "files": list(corr.get("files", [])) or list(active.files or []),
                    "source_commit_sha": sha,
                }
                ckpt.save_correction_task(corr_task, attempt=next_attempt)
                ckpt.set_phase(TaskPhase.CORRECTING)
                continue

            if phase == TaskPhase.CORRECTING.value:
                ct = delivery.get("correction_task") or {}
                ckpt.set_phase(TaskPhase.EXECUTING, active_task_id=ct.get("id"), review_attempt=1, last_review_reason="", review_snapshot=None, pending_commit_message=None, pre_commit_sha=None, push_status=None, ci_status=None, ci_runs=None, ci_failed_logs=None)
                continue

            return AgentResult(status="FAILED", message=f"unknown phase: {phase}", artifacts=[])
