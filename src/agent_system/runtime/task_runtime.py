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
                # Handle verification/test no-code tasks
                if active.role == "test" or active.type == "verification":
                    if not active.validation:
                        return AgentResult(status="FAILED", message=f"verification task {active.id} has no validation commands", artifacts=[])
                    # Ensure clean workspace
                    snap0 = self.git.capture_tree_snapshot()
                    if snap0.has_changes:
                        return AgentResult(status="FAILED", message=f"verification task {active.id} requires clean workspace: {snap0.changed_files}", artifacts=snap0.changed_files)
                    base = self.git.head_sha()
                    tree_pre = self.git.snapshot_worktree_tree() or self.git.head_tree_sha()
                    ckpt.enter_validating(reviewed_tree_sha=tree_pre, base_commit_sha=base)
                    delivery = (ckpt.state.load().get("delivery") or {})
                    phase = delivery.get("phase")
                    continue
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
                    exc_to_raise = None
                    result = None
                    try:
                        result = CodeAgent(backend=self.coding_backend, root=self.root).execute(exec_task, baseline=baseline)
                    except BaseException as exc:
                        exc_to_raise = exc
                        # capture after state even on interrupt
                        try:
                            git_after = capture_git_control_state(self.root)
                            violation = validate_unchanged(git_before, git_after)
                            if violation:
                                raise RuntimeError(violation) from exc
                        except RuntimeError:
                            raise
                        except Exception:
                            pass
                        raise
                    finally:
                        if exc_to_raise is None:
                            git_after = capture_git_control_state(self.root)
                            violation = validate_unchanged(git_before, git_after)
                            if violation:
                                return AgentResult(status="FAILED", message=violation, artifacts=[], baseline=getattr(result, "baseline", None) if result else None, evidence=getattr(result, "evidence", None) if result else None, execution_status="ERROR", stop_reason="runtime_authority_violation")
                    if exc_to_raise is not None:
                        raise exc_to_raise
                    git_after = capture_git_control_state(self.root)
                    violation = validate_unchanged(git_before, git_after)
                    if violation:
                        return AgentResult(status="FAILED", message=violation, artifacts=[], baseline=getattr(result, "baseline", None), evidence=getattr(result, "evidence", None), execution_status="ERROR", stop_reason="runtime_authority_violation")
                else:
                    return AgentResult(status="FAILED", message=f"unsupported task role: {exec_task.role}", artifacts=[])
                if getattr(result, "execution_status", "COMPLETED") == "ERROR" or result.status in ("FAILED", "INCOMPLETE"):
                    return AgentResult(status="FAILED", message=result.message, artifacts=result.artifacts)
                # Capture tree snapshot
                snap = self.git.capture_tree_snapshot()
                from agent_system.agents.models import execution_evidence_to_dict
                ckpt.enter_validating(reviewed_tree_sha=snap.tree_sha, base_commit_sha=snap.base_commit_sha, validation_snapshot=None)
                continue

            if phase == TaskPhase.VALIDATING.value:
                reviewed_tree = delivery.get("reviewed_tree_sha") or ""
                base_sha = delivery.get("base_commit_sha") or ""
                # Re-snapshot to get diff/files
                snap = self.git.capture_tree_snapshot()
                # If verification task, run validation directly
                if active.role == "test" or active.type == "verification":
                    if not active.validation:
                        return AgentResult(status="FAILED", message=f"verification task {active.id} has no validation", artifacts=[])
                    # Check still clean before validation
                    if snap.tree_sha != reviewed_tree and reviewed_tree:
                        # tree changed before validation
                        return AgentResult(status="FAILED", message="Reviewed tree no longer matches workspace.", artifacts=[])
                    from agent_system.runtime.validation import get_validation_runner
                    runner = get_validation_runner(self.root)
                    before_tree = self.git.snapshot_worktree_tree() or ""
                    vres = runner.validate(active, reviewed_tree or before_tree)
                    after_tree = self.git.snapshot_worktree_tree() or ""
                    # Validation must not mutate tree
                    if after_tree != (reviewed_tree or before_tree):
                        return AgentResult(status="FAILED", message="Validation mutated project tree; failing closed.", artifacts=[])
                    snap2 = self.git.capture_tree_snapshot()
                    vsnap = {"tree_sha": vres.tree_sha or reviewed_tree or before_tree, "status": vres.status, "commands": [{"command": c.command, "exit_code": c.exit_code, "status": c.status, "output": c.output[:2000]} for c in vres.commands]}
                    if vres.status != "PASSED":
                        return AgentResult(status="FAILED", message=f"verification validation failed for {active.id}", artifacts=[])
                    ckpt.update_delivery(validation_snapshot=vsnap)
                    # Direct to REVIEWING then complete as VERIFIED
                    ckpt.enter_reviewing(review_snapshot={"result_status": "SUCCESS", "result_message": "verification", "result_artifacts": [], "commit_message": "", "outcome_status": "VERIFIED", "execution_status": "COMPLETED", "stop_reason": None, "evidence": None, "project_diff": snap2.diff, "changed_files": snap2.changed_files, "project_fingerprint": snap2.tree_sha, "active_task_id": active.id, "reviewed_tree_sha": reviewed_tree or snap2.tree_sha, "base_commit_sha": base_sha or snap2.base_commit_sha, "validation_snapshot": vsnap})
                    # Fall through to REVIEWING
                    continue
                # Normal code path: optionally run declared validation
                if active.validation:
                    cur_tree = snap.tree_sha
                    if reviewed_tree and cur_tree != reviewed_tree:
                        return AgentResult(status="FAILED", message="Reviewed tree no longer matches workspace.", artifacts=[])
                    from agent_system.runtime.validation import get_validation_runner
                    runner = get_validation_runner(self.root)
                    before_tree = self.git.snapshot_worktree_tree() or ""
                    vres = runner.validate(active, reviewed_tree or before_tree)
                    after_tree = self.git.snapshot_worktree_tree() or ""
                    if after_tree != (reviewed_tree or before_tree):
                        return AgentResult(status="FAILED", message="Validation mutated project tree; failing closed.", artifacts=[])
                    vsnap = {"tree_sha": vres.tree_sha or reviewed_tree or before_tree, "status": vres.status, "commands": [{"command": c.command, "exit_code": c.exit_code, "status": c.status, "output": c.output[:2000]} for c in vres.commands]}
                    ckpt.update_delivery(validation_snapshot=vsnap)
                    if vres.status != "PASSED":
                        return AgentResult(status="FAILED", message=f"validation failed for {active.id}", artifacts=[])
                # Build review snapshot and enter REVIEWING
                from agent_system.agents.models import execution_evidence_from_dict, task_baseline_from_dict
                # Retrieve last result snapshot if any? For normal flow we need to reconstruct
                # Use current snap for diff
                snapsnap = delivery.get("review_snapshot") or {}
                # If we have no prior result snapshot, use minimal
                result_status = snapsnap.get("result_status", "SUCCESS")
                result_message = snapsnap.get("result_message", "executed")
                result_artifacts = snapsnap.get("result_artifacts", [])
                # Also try from EXECUTING's last evidence baseline
                baseline = task_baseline_from_dict(delivery.get("task_baseline"))
                vsnap2 = delivery.get("validation_snapshot")
                # Build evidence dict
                evidence_dict = snapsnap.get("evidence")
                ckpt.enter_reviewing(review_snapshot={"result_status": result_status, "result_message": result_message, "result_artifacts": result_artifacts, "commit_message": snapsnap.get("commit_message", "") or "", "outcome_status": snapsnap.get("outcome_status", "") or "", "execution_status": snapsnap.get("execution_status", "COMPLETED") or "COMPLETED", "stop_reason": snapsnap.get("stop_reason"), "evidence": evidence_dict, "project_diff": snap.diff, "changed_files": list(snap.changed_files or []), "project_fingerprint": snap.tree_sha, "active_task_id": active.id, "reviewed_tree_sha": reviewed_tree or snap.tree_sha, "base_commit_sha": base_sha or snap.base_commit_sha, "validation_snapshot": vsnap2})
                continue

            if phase == TaskPhase.REVIEWING.value:
                # Re-validate tree identity
                current_snap = self.git.capture_tree_snapshot()
                snap = delivery.get("review_snapshot") or {}
                reviewed_tree = snap.get("reviewed_tree_sha") or ""
                if reviewed_tree and current_snap.tree_sha != reviewed_tree:
                    return AgentResult(status="FAILED", message="Reviewed tree no longer matches workspace.", artifacts=[])
                from agent_system.agents.models import execution_evidence_from_dict, task_baseline_from_dict
                commit_message = snap.get("commit_message", "")
                outcome_status = snap.get("outcome_status", "")
                baseline = task_baseline_from_dict(delivery.get("task_baseline"))
                evidence = execution_evidence_from_dict(snap.get("evidence"))
                tmp_result = AgentResult(status=snap.get("result_status", "SUCCESS"), message=snap.get("result_message", ""), artifacts=snap.get("result_artifacts", []), baseline=baseline, evidence=evidence, execution_status=snap.get("execution_status", "COMPLETED") or "COMPLETED", stop_reason=snap.get("stop_reason"))
                if outcome_status:
                    from agent_system.agents.models import TaskOutcome
                    tmp_result.outcome = TaskOutcome(task_id=active.id, status=outcome_status)
                project_diff = snap.get("project_diff", "")
                vsnap = snap.get("validation_snapshot") or delivery.get("validation_snapshot")
                # Optional/skipped handling before LLM
                if active.type == "optional":
                    # If no diff and not satisfied, allow SKIPPED if clean
                    if not project_diff.strip() and not snap.get("changed_files"):
                        if not current_snap.has_changes:
                            ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="SKIPPED", commit_sha=None, push_status="SKIPPED", ci_status="SKIPPED")
                            return AgentResult(status="SUCCESS", message=f"task {active.id} skipped", artifacts=[])
                if active.role == "test" and active.type == "verification":
                    # Already validated
                    if vsnap and vsnap.get("status") == "PASSED":
                        if current_snap.has_changes:
                            return AgentResult(status="FAILED", message="verification task has pending changes", artifacts=current_snap.changed_files)
                        ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="VERIFIED", commit_sha=None, push_status="SKIPPED", ci_status="SKIPPED")
                        return AgentResult(status="SUCCESS", message=f"task {active.id} verified", artifacts=[])
                    return AgentResult(status="FAILED", message="verification validation failed", artifacts=[])
                review = self.tech_lead.review(active, tmp_result, project_diff)
                if commit_message and not getattr(review, "commit_message", ""):
                    review.commit_message = commit_message
                if review.status == "SUCCESS":
                    if getattr(review, "outcome", None) and review.outcome.status == "SATISFIED":
                        changes_now = self.git.capture_tree_snapshot()
                        if changes_now.has_changes:
                            return AgentResult(status="FAILED", message=f"Runtime invariant violation: SATISFIED task has pending project changes: {changes_now.changed_files}", artifacts=changes_now.changed_files)
                        ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="SATISFIED", commit_sha=None, push_status="SKIPPED", ci_status="SKIPPED")
                        return AgentResult(status="SUCCESS", message=review.message, artifacts=review.artifacts)
                    if getattr(review, "outcome", None) and review.outcome.status in ("VERIFIED", "SKIPPED"):
                        out = review.outcome.status
                        changes_now = self.git.capture_tree_snapshot()
                        if changes_now.has_changes:
                            return AgentResult(status="FAILED", message=f"{out} task has pending changes", artifacts=changes_now.changed_files)
                        ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome=out, commit_sha=None, push_status="SKIPPED", ci_status="SKIPPED")
                        return AgentResult(status="SUCCESS", message=review.message, artifacts=review.artifacts)
                    if not review.commit_message:
                        return AgentResult(status="FAILED", message="Approved changed task has no commit message", artifacts=tmp_result.artifacts)
                    # Create commit intent
                    snap2 = self.git.capture_tree_snapshot()
                    if snap2.tree_sha != reviewed_tree:
                        return AgentResult(status="FAILED", message="Reviewed tree no longer matches workspace.", artifacts=[])
                    head_ref = self.git.head_ref()
                    if not head_ref:
                        return AgentResult(status="FAILED", message="detached HEAD not supported", artifacts=[])
                    commit_intent = {"tree_sha": snap2.tree_sha, "parent_sha": snap2.base_commit_sha, "message": review.commit_message, "head_ref": head_ref, "pending_commit_sha": None}
                    ckpt.enter_committing(pending_commit_message=review.commit_message, pre_commit_sha=snap2.base_commit_sha, commit_intent=commit_intent)
                    continue
                attempt = int(delivery.get("review_attempt", 1))
                if attempt >= MAX_REVIEW_ATTEMPTS:
                    return review
                ckpt.set_phase(TaskPhase.EXECUTING, review_attempt=attempt + 1, last_review_reason=review.message, review_snapshot=None)
                continue

            if phase == TaskPhase.COMMITTING.value:
                commit_intent = delivery.get("commit_intent") or {}
                pending = delivery.get("pending_commit_message", "") or commit_intent.get("message", "")
                pre_sha = delivery.get("pre_commit_sha") or commit_intent.get("parent_sha", "")
                head_ref = commit_intent.get("head_ref") or self.git.head_ref()
                tree_sha = commit_intent.get("tree_sha") or ""
                pending_sha = commit_intent.get("pending_commit_sha") or ""
                cur_sha = self.git.head_sha()
                # Crash recovery: if pending_commit_sha exists and HEAD == pending, success
                if pending_sha and cur_sha == pending_sha:
                    # verify
                    ct = self.git.commit_tree_sha(pending_sha)
                    if ct != tree_sha:
                        return AgentResult(status="FAILED", message="commit tree mismatch on recovery", artifacts=[])
                    self.git.sync_index_to_head()
                    ckpt.update_delivery(commit_sha=pending_sha)
                    # freeze push target if not already
                    snap = self.git.capture_tree_snapshot()
                    target = self.git.resolve_push_target(commit_sha=pending_sha)
                    if target is None:
                        push_target = delivery.get("push_target")
                    else:
                        push_target = {"commit_sha": target.commit_sha, "local_ref": target.local_ref, "remote": target.remote, "remote_url": target.remote_url, "target_ref": target.target_ref}
                    cfg = DeliveryConfig.load(self.root)
                    if cfg.mode == "local":
                        ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=pending_sha, push_status="SKIPPED", ci_status="SKIPPED")
                        return AgentResult(status="SUCCESS", message=f"committed {pending_sha[:7]}", artifacts=[])
                    ckpt.enter_pushing(commit_sha=pending_sha, push_target=push_target)
                    continue
                # If pending exists but HEAD still parent, try to advance
                if pending_sha and cur_sha == pre_sha:
                    if pending_sha:
                        ct = self.git.commit_tree_sha(pending_sha)
                        if ct == tree_sha:
                            ok = self.git.update_ref(head_ref, pending_sha, pre_sha) if pre_sha else self.git.update_ref(head_ref, pending_sha)
                            if ok:
                                self.git.sync_index_to_head()
                                cfg = DeliveryConfig.load(self.root)
                                if cfg.mode == "local":
                                    ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=pending_sha, push_status="SKIPPED", ci_status="SKIPPED")
                                    return AgentResult(status="SUCCESS", message=f"committed {pending_sha[:7]}", artifacts=[])
                                target = self.git.resolve_push_target(commit_sha=pending_sha)
                                push_target = {"commit_sha": target.commit_sha, "local_ref": target.local_ref, "remote": target.remote, "remote_url": target.remote_url, "target_ref": target.target_ref} if target else delivery.get("push_target")
                                ckpt.enter_pushing(commit_sha=pending_sha, push_target=push_target)
                                continue
                # If HEAD already advanced externally with correct message/tree
                if cur_sha and pre_sha and cur_sha != pre_sha:
                    parent = self.git.commit_parent(cur_sha)
                    subj = self.git.commit_subject(cur_sha)
                    ct = self.git.commit_tree_sha(cur_sha)
                    if parent == (pre_sha or "") and subj == (pending or "") and ct == (tree_sha or ct):
                        self.git.sync_index_to_head()
                        cfg = DeliveryConfig.load(self.root)
                        if cfg.mode == "local":
                            ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=cur_sha, push_status="SKIPPED", ci_status="SKIPPED")
                            return AgentResult(status="SUCCESS", message=f"committed {cur_sha[:7]}", artifacts=[])
                        target = self.git.resolve_push_target(commit_sha=cur_sha)
                        push_target = {"commit_sha": target.commit_sha, "local_ref": target.local_ref, "remote": target.remote, "remote_url": target.remote_url, "target_ref": target.target_ref} if target else None
                        ckpt.enter_pushing(commit_sha=cur_sha, push_target=push_target)
                        continue
                    if cur_sha != pre_sha:
                        return AgentResult(status="FAILED", message="COMMITTING checkpoint inconsistent: HEAD advanced unexpectedly", artifacts=[])
                if not self.git.is_workspace_repo():
                    return AgentResult(status="FAILED", message="workspace not a Git repository", artifacts=[])
                snap = self.git.capture_tree_snapshot()
                if not snap.has_changes:
                    return AgentResult(status="FAILED", message="COMMITTING checkpoint inconsistent: approved changed task has no pending project changes", artifacts=[])
                if not tree_sha:
                    tree_sha = snap.tree_sha
                    head_ref = self.git.head_ref()
                    pre_sha = snap.base_commit_sha
                    commit_intent = {"tree_sha": tree_sha, "parent_sha": pre_sha, "message": pending, "head_ref": head_ref, "pending_commit_sha": None}
                    ckpt.update_delivery(commit_intent=commit_intent, pre_commit_sha=pre_sha)
                # Phase 1: commit-tree
                if not pending_sha:
                    pending_sha = self.git.create_commit_object(tree_sha, pre_sha, pending)
                    if not pending_sha:
                        return AgentResult(status="FAILED", message=f"commit failed for {original.id}: commit-tree failed", artifacts=[])
                    ct = self.git.commit_tree_sha(pending_sha)
                    if ct != tree_sha:
                        return AgentResult(status="FAILED", message="commit tree mismatch", artifacts=[])
                    commit_intent["pending_commit_sha"] = pending_sha
                    ckpt.update_delivery(commit_intent=commit_intent, pending_commit_sha=pending_sha)
                # Phase 2: update-ref
                ok = self.git.update_ref(head_ref, pending_sha, pre_sha) if pre_sha else self.git.update_ref(head_ref, pending_sha)
                if not ok:
                    return AgentResult(status="FAILED", message="update-ref failed (branch changed externally?)", artifacts=[])
                self.git.sync_index_to_head()
                # verify HEAD
                new_head = self.git.head_sha()
                if new_head != pending_sha:
                    return AgentResult(status="FAILED", message="HEAD not at pending commit after update-ref", artifacts=[])
                target = self.git.resolve_push_target(commit_sha=pending_sha)
                push_target = {"commit_sha": target.commit_sha, "local_ref": target.local_ref, "remote": target.remote, "remote_url": target.remote_url, "target_ref": target.target_ref} if target else None
                cfg = DeliveryConfig.load(self.root)
                if cfg.mode == "local":
                    ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=pending_sha, push_status="SKIPPED", ci_status="SKIPPED")
                    return AgentResult(status="SUCCESS", message=f"committed {pending_sha[:7]}", artifacts=[])
                ckpt.enter_pushing(commit_sha=pending_sha, push_target=push_target)
                continue

            if phase == TaskPhase.PUSHING.value:
                sha = delivery.get("commit_sha", "")
                if not sha:
                    return AgentResult(status="FAILED", message="PUSHING requires commit_sha", artifacts=[])
                push_target_dict = delivery.get("push_target")
                if not push_target_dict:
                    return AgentResult(status="FAILED", message="PUSHING requires push_target", artifacts=[])
                from agent_system.runtime.git import PushTarget as _PT
                # Validate frozen target hasn't drifted
                head = self.git.head_sha()
                if head != sha:
                    return AgentResult(status="FAILED", message=f"PUSHING HEAD mismatch: {head} != {sha}", artifacts=[])
                cur_ref = self.git.head_ref()
                if cur_ref != push_target_dict.get("local_ref"):
                    return AgentResult(status="FAILED", message="PUSHING local ref mismatch", artifacts=[])
                cur_url = ""
                rr = self.git.shell.run(["git", "remote", "get-url", push_target_dict.get("remote", "")])
                if rr.returncode == 0:
                    cur_url = rr.stdout.strip()
                if push_target_dict.get("remote_url") and cur_url != push_target_dict.get("remote_url"):
                    return AgentResult(status="FAILED", message="PUSHING remote URL changed", artifacts=[])
                cfg = DeliveryConfig.load(self.root)
                if cfg.mode == "local":
                    ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status="SKIPPED", ci_status="SKIPPED")
                    return AgentResult(status="SUCCESS", message="local mode push skipped", artifacts=[])
                target = _PT(commit_sha=push_target_dict["commit_sha"], local_ref=push_target_dict["local_ref"], remote=push_target_dict["remote"], remote_url=push_target_dict["remote_url"], target_ref=push_target_dict["target_ref"])
                push_res = self.git.push_exact(target)
                if push_res["status"] == "SUCCESS":
                    ckpt.enter_ci_discovery(commit_sha=sha)
                    continue
                # In GH mode, any push failure is terminal FAILED
                if cfg.mode == "gh":
                    return AgentResult(status="FAILED", message=f"push failed: {push_res.get('message','')[:200]}", artifacts=[])
                return AgentResult(status="FAILED", message=f"push failed: {push_res.get('message','')[:200]}", artifacts=[])

            if phase == TaskPhase.CI_DISCOVERY.value:
                sha = delivery.get("commit_sha", "")
                disc = self._ci_discover(sha)
                if disc["status"] == "CI_DISCOVERY_ERROR":
                    return AgentResult(status="FAILED", message=f"CI discovery error: {disc.get('message','')[:200]}", artifacts=[])
                if disc["status"] == "CI_NOT_DETECTED":
                    ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status="SUCCESS", ci_status="CI_NOT_DETECTED")
                    return AgentResult(status="SUCCESS", message="CI not detected", artifacts=[])
                ckpt.enter_waiting_ci(ci_runs=disc.get("runs", []), commit_sha=sha, frozen_ci_runs=disc.get("runs", []))
                continue

            if phase == TaskPhase.WAITING_CI.value:
                sha = delivery.get("commit_sha", "")
                runs = delivery.get("frozen_ci_runs") or delivery.get("ci_runs") or []
                if not runs and sha:
                    disc = self._ci_discover(sha)
                    if disc["status"] == "CI_DISCOVERY_ERROR":
                        return AgentResult(status="FAILED", message=f"CI discovery error: {disc.get('message','')[:200]}", artifacts=[])
                    runs = disc.get("runs", [])
                    if not runs:
                        ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status="SUCCESS", ci_status="CI_NOT_DETECTED")
                        return AgentResult(status="SUCCESS", message="CI not detected", artifacts=[])
                    ckpt.set_phase(TaskPhase.WAITING_CI, event="enter_waiting_ci", ci_runs=runs, frozen_ci_runs=runs)
                ci_res = self._ci_wait(runs)
                if ci_res["status"] == "CI_PASSED":
                    ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status="SUCCESS", ci_status="CI_PASSED", ci_runs=ci_res.get("runs", runs))
                    return AgentResult(status="SUCCESS", message="CI passed", artifacts=[])
                if ci_res["status"] == "CI_NOT_DETECTED":
                    ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status="SUCCESS", ci_status="CI_NOT_DETECTED")
                    return AgentResult(status="SUCCESS", message="CI not detected", artifacts=[])
                ckpt.enter_ci_review(ci_status="CI_FAILED", ci_failed_logs=ci_res.get("failed_logs", ""), ci_runs=ci_res.get("runs", runs), commit_sha=sha)
                ckpt.update_delivery(ci_runs=ci_res.get("runs", runs))
                continue

            if phase == TaskPhase.CI_REVIEW.value:
                # Refresh CI status if rerun succeeded
                sha = delivery.get("commit_sha", "")
                runs = delivery.get("frozen_ci_runs") or delivery.get("ci_runs") or []
                if runs and sha:
                    ci_res = self._ci_wait(runs)
                    if ci_res["status"] == "CI_PASSED":
                        ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status="SUCCESS", ci_status="CI_PASSED", ci_runs=ci_res.get("runs", runs))
                        return AgentResult(status="SUCCESS", message="CI passed on refresh", artifacts=[])
                ci_logs = delivery.get("ci_failed_logs", "")
                sha = delivery.get("commit_sha", "")
                decision = self.tech_lead.ci_review(ci_status="CI_FAILED", ci_logs=ci_logs, task=active, commit_sha=sha)
                if decision.get("decision") in ("APPROVED",):
                    ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="CHANGED", commit_sha=sha, push_status="SUCCESS", ci_status=decision.get("decision"))
                    return AgentResult(status="SUCCESS", message=decision.get("reason", "CI approved"), artifacts=[])
                if decision.get("decision") == "APPROVED_WITH_NOTE":
                    return AgentResult(status="FAILED", message=f"CI classified as {decision.get('classification','')} — workflow FAILED", artifacts=[])
                if decision.get("decision") != "CHANGES_REQUIRED":
                    return AgentResult(status="FAILED", message=f"CI review invalid decision: {decision.get('decision')}", artifacts=[])
                corr = decision.get("correction")
                if not isinstance(corr, dict) or not corr.get("description"):
                    return AgentResult(status="FAILED", message="CI review CHANGES_REQUIRED without structured correction", artifacts=[])
                if decision.get("classification") in ("EXISTING_PROJECT_FAILURE", "INFRASTRUCTURE_FAILURE", "UNKNOWN"):
                    return AgentResult(status="FAILED", message=f"CI {decision.get('classification')} — workflow FAILED", artifacts=[])
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
                ckpt.set_phase(TaskPhase.EXECUTING, event="enter_executing", active_task_id=ct.get("id"), review_attempt=1, last_review_reason="", review_snapshot=None, pending_commit_message=None, pre_commit_sha=None, commit_intent=None, push_status=None, ci_status=None, ci_runs=None, ci_failed_logs=None, push_target=None, frozen_ci_runs=None, validation_snapshot=None, reviewed_tree_sha=None, base_commit_sha=None)
                continue

            return AgentResult(status="FAILED", message=f"unknown phase: {phase}", artifacts=[])

    def _ci_discover(self, sha):
        from agent_system.runtime.ci_monitor import CIMonitor
        return CIMonitor(self.root).discover_for_commit(sha)

    def _ci_wait(self, runs):
        from agent_system.runtime.ci_monitor import CIMonitor
        return CIMonitor(self.root).wait_for_runs(runs)
