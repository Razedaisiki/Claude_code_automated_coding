from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_system.agents.models import AgentResult, AgentTask
from agent_system.runtime.git import Git

MAX_REVIEW_ATTEMPTS = 3
MAX_CI_CORRECTIONS = 3
MAX_REVIEW_TEXT_BYTES = 512 * 1024

class TaskRuntime:
    def __init__(self, root: Path, *, coding_backend, tech_lead, project_context=None):
        self.root = Path(root).resolve() if root else Path.cwd().resolve()
        self.coding_backend = coding_backend
        self.tech_lead = tech_lead
        self.project_context = project_context
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

    def _relevant_files_union(self, active: AgentTask, original: AgentTask, snap, candidate_artifacts):
        files = set()
        for f in (original.files or []):
            if isinstance(f, str) and f.strip():
                files.add(f.strip())
        for f in (active.files or []):
            if isinstance(f, str) and f.strip():
                files.add(f.strip())
        for f in (snap.changed_files or []):
            if f and f.strip() and ".agent/" not in f:
                files.add(f.strip())
        for a in (candidate_artifacts or []):
            if isinstance(a, str) and a.strip() and "/" in a and not a.strip().startswith("/"):
                if ".agent/" not in a:
                    files.add(a.strip())
        return sorted(files)

    def _build_relevant_file_evidence(self, tree_sha: str, paths: list) -> list:
        evidences = []
        total = 0
        for rel in paths:
            rel = rel.strip()
            if not rel or rel.startswith("/") or ".." in Path(rel).parts:
                continue
            meta = self.git.read_tree_path(tree_sha, rel)
            if meta is None:
                evidences.append({"path": rel, "kind": "missing", "missing": True})
                continue
            mode = meta.get("mode", "")
            sha = meta.get("sha", "")
            if mode == "120000":
                data = self.git.cat_blob(sha)
                try:
                    target = data.decode("utf-8", errors="strict").strip()
                except Exception:
                    target = data.decode("utf-8", errors="replace").strip()
                evidences.append({"path": rel, "kind": "symlink", "symlink_target": target, "blob_sha": sha})
                continue
            size = self.git.blob_size(sha)
            data = self.git.cat_blob(sha)
            binary = self.git.is_binary_blob(data)
            if binary:
                evidences.append({"path": rel, "kind": "file", "blob_sha": sha, "size": size, "binary": True})
                continue
            try:
                text = data.decode("utf-8")
            except Exception:
                text = data.decode("utf-8", errors="replace")
            if total + len(data) > MAX_REVIEW_TEXT_BYTES:
                raise RuntimeError(f"Review package exceeds supported context size: {total + len(data)} > {MAX_REVIEW_TEXT_BYTES}.")
            total += len(data)
            evidences.append({"path": rel, "kind": "file", "blob_sha": sha, "size": size, "binary": False, "content": text})
        if total > MAX_REVIEW_TEXT_BYTES:
            raise RuntimeError(f"Review package exceeds supported context size: {total} > {MAX_REVIEW_TEXT_BYTES}.")
        return evidences

    def run_task(self, original: AgentTask, task_index: int, resume_current: bool = False, project_context=None) -> AgentResult:
        from agent_system.agents.code_agent import CodeAgent
        from agent_system.delivery import DeliveryConfig
        from agent_system.runtime.checkpoint import Checkpoint, TaskPhase
        ckpt = Checkpoint(self.root)
        if not resume_current:
            ckpt.begin_task(task_index, original.id)
        ctx = project_context or self.project_context
        while True:
            delivery = (ckpt.state.load().get("delivery") or {})
            phase = delivery.get("phase") or TaskPhase.EXECUTING.value
            active = self._resolve_active_task(original, delivery)
            if phase == TaskPhase.TASK_COMPLETED.value:
                return AgentResult(status="SUCCESS", message=f"task {original.id} completed", artifacts=[])
            if phase == TaskPhase.EXECUTING.value:
                if active.role == "test" or active.type == "verification":
                    if not active.validation:
                        return AgentResult(status="FAILED", message=f"verification task {active.id} has no validation", artifacts=[])
                    snap0 = self.git.capture_tree_snapshot()
                    if snap0.has_changes:
                        return AgentResult(status="FAILED", message=f"verification task {active.id} requires clean workspace: {snap0.changed_files}", artifacts=snap0.changed_files)
                    base = self.git.head_sha()
                    tree_pre = self.git.snapshot_worktree_tree() or self.git.head_tree_sha()
                    from agent_system.runtime.review_store import ReviewArtifactStore
                    state = ckpt.state.load()
                    sid = state.get("session_id") or ""
                    attempt = int(delivery.get("review_attempt", 1) or 1)
                    candidate_obj = {"task_id": active.id, "original_task_id": original.id, "review_attempt": attempt, "reviewed_tree_sha": tree_pre, "base_commit_sha": base or "", "base_tree_sha": self.git.head_tree_sha() if base else "", "changed_files": [], "project_diff": "", "result_status": "SUCCESS", "result_message": "verification", "result_artifacts": [], "execution_status": "COMPLETED", "stop_reason": None, "execution_evidence": None}
                    if sid:
                        store = ReviewArtifactStore(self.root, sid)
                        cand_ref = store.write_candidate(active.id, attempt, candidate_obj)
                    else:
                        cand_ref = {"path": "", "sha256": hashlib.sha256(json.dumps(candidate_obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
                    ckpt.enter_validating(reviewed_tree_sha=tree_pre, base_commit_sha=base or "", candidate_ref=cand_ref)
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
                snap = self.git.capture_tree_snapshot()
                from agent_system.agents.models import execution_evidence_to_dict
                from agent_system.runtime.review_store import ReviewArtifactStore
                state = ckpt.state.load()
                sid = state.get("session_id") or ""
                attempt = int(delivery.get("review_attempt", 1) or 1)
                candidate_obj = {"task_id": active.id, "original_task_id": original.id, "review_attempt": attempt, "reviewed_tree_sha": snap.tree_sha, "base_commit_sha": snap.base_commit_sha or "", "base_tree_sha": snap.base_tree_sha or "", "changed_files": snap.changed_files, "project_diff": snap.diff, "result_status": getattr(result, "status", "SUCCESS") or "SUCCESS", "result_message": getattr(result, "message", "") or "", "result_artifacts": list(getattr(result, "artifacts", None) or []), "execution_status": getattr(result, "execution_status", "COMPLETED") or "COMPLETED", "stop_reason": getattr(result, "stop_reason", None), "execution_evidence": execution_evidence_to_dict(getattr(result, "evidence", None))}
                if sid:
                    store = ReviewArtifactStore(self.root, sid)
                    cand_ref = store.write_candidate(active.id, attempt, candidate_obj)
                else:
                    cand_ref = {"path": "", "sha256": hashlib.sha256(json.dumps(candidate_obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
                ckpt.enter_validating(reviewed_tree_sha=snap.tree_sha, base_commit_sha=snap.base_commit_sha or "", candidate_ref=cand_ref)
                continue
            if phase == TaskPhase.VALIDATING.value:
                reviewed_tree = delivery.get("reviewed_tree_sha") or ""
                base_sha = delivery.get("base_commit_sha") or ""
                cand_ref = delivery.get("candidate_ref")
                if not cand_ref:
                    return AgentResult(status="FAILED", message="VALIDATING requires candidate_ref", artifacts=[])
                # Permanent fix: Already-satisfied with no diff should not require VALIDATING
                # Many plans mark error-path tasks as implementation with no file gap
                try:
                    _cur = self.git.capture_tree_snapshot()
                    _has_diff = bool(_cur.diff.strip()) or bool(_cur.changed_files)
                    if not _has_diff:
                        # Peek candidate: if result already says Already satisfied / no changes, skip validation
                        _cand_peek = None
                        try:
                            _state_peek = ckpt.state.load()
                            _sid_peek = _state_peek.get("session_id") or ""
                            if _sid_peek and isinstance(cand_ref, dict) and cand_ref.get("path"):
                                from agent_system.runtime.review_store import ReviewArtifactStore as _StorePeek
                                _cand_peek = _StorePeek(self.root, _sid_peek).load_candidate(cand_ref)
                            elif isinstance(cand_ref, dict) and cand_ref.get("reviewed_tree_sha"):
                                _cand_peek = cand_ref
                        except Exception:
                            _cand_peek = None
                        _msg = str(_cand_peek.get("result_message", "")) if isinstance(_cand_peek, dict) else ""
                        if "Already satisfied" in _msg:
                            # Directly build review package as SATISFIED path without re-validating
                            from agent_system.runtime.review_store import ReviewArtifactStore as _StorePkg
                            _state_pkg = ckpt.state.load()
                            _sid_pkg = _state_pkg.get("session_id") or ""
                            _candidate_pkg = _cand_peek or {}
                            _vsnap_ro = {"task_id": active.id, "tree_sha": reviewed_tree or _cur.tree_sha, "status": "PASSED", "checks": [], "summary": "Already satisfied — no diff, validation skipped", "tool_events": []}
                            # Write a synthetic validation reflecting already-satisfied
                            try:
                                if _sid_pkg:
                                    _store_v = _StorePkg(self.root, _sid_pkg)
                                    _attempt_ro = int(_candidate_pkg.get("review_attempt", 1) or 1)
                                    ckpt.update_delivery(validation_ref=_store_v.write_validation(active.id, _attempt_ro, _vsnap_ro))
                            except Exception:
                                ckpt.update_delivery(validation_ref={"path": "", "sha256": hashlib.sha256(json.dumps(_vsnap_ro, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), "status": "PASSED"})
                            print(f"  VALIDATING: Already satisfied with no diff, skipping validation for {active.id}")
                            # Fall through to review package build below by continuing loop? We stay in VALIDATING
                            # Build minimal review package to enter REVIEWING
                            _relevant = self._relevant_files_union(active, original, _cur, _candidate_pkg.get("result_artifacts", []))
                            try:
                                _rf = self._build_relevant_file_evidence(reviewed_tree or _cur.tree_sha, _relevant)
                            except RuntimeError as e:
                                return AgentResult(status="FAILED", message=str(e), artifacts=[])
                            _pkg_obj = {"package_version": 1, "session_id": _sid_pkg, "task_id": active.id, "original_task_id": original.id, "review_attempt": int(_candidate_pkg.get("review_attempt", 1) or 1), "reviewed_tree_sha": reviewed_tree or _cur.tree_sha, "base_commit_sha": base_sha or _candidate_pkg.get("base_commit_sha", ""), "base_tree_sha": _candidate_pkg.get("base_tree_sha", "") or "", "changed_files": [], "project_diff": "", "relevant_files": _rf, "candidate": _candidate_pkg, "validation": _vsnap_ro, "task_baseline": delivery.get("task_baseline")}
                            if _sid_pkg:
                                _store_pkg2 = _StorePkg(self.root, _sid_pkg)
                                _pkg_ref = _store_pkg2.write_review_package(active.id, int(_candidate_pkg.get("review_attempt", 1) or 1), _pkg_obj)
                            else:
                                _pkg_ref = {"path": "", "sha256": hashlib.sha256(json.dumps(_pkg_obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
                            ckpt.enter_reviewing(review_package_ref=_pkg_ref, reviewed_tree_sha=_pkg_obj["reviewed_tree_sha"], base_commit_sha=_pkg_obj["base_commit_sha"])
                            continue
                except Exception:
                    pass
                try:
                    state = ckpt.state.load()
                    sid = state.get("session_id") or ""
                    if sid and isinstance(cand_ref, dict) and cand_ref.get("path"):
                        from agent_system.runtime.review_store import ReviewArtifactStore
                        store = ReviewArtifactStore(self.root, sid)
                        candidate = store.load_candidate(cand_ref)
                    else:
                        candidate = cand_ref if isinstance(cand_ref, dict) and "reviewed_tree_sha" in cand_ref else {}
                except Exception as e:
                    return AgentResult(status="FAILED", message=f"candidate artifact verification failed: {e}", artifacts=[])
                if not isinstance(candidate, dict) or not candidate.get("reviewed_tree_sha"):
                    return AgentResult(status="FAILED", message="candidate artifact invalid", artifacts=[])
                if candidate.get("reviewed_tree_sha") != reviewed_tree:
                    return AgentResult(status="FAILED", message="candidate tree mismatch", artifacts=[])
                if candidate.get("task_id") != active.id:
                    return AgentResult(status="FAILED", message="candidate task_id mismatch", artifacts=[])
                cur_snap = self.git.capture_tree_snapshot()
                if cur_snap.tree_sha != reviewed_tree:
                    return AgentResult(status="FAILED", message="Reviewed tree no longer matches workspace.", artifacts=[])
                vsnap_obj = None
                val_ref = delivery.get("validation_ref")
                if val_ref:
                    try:
                        state2 = ckpt.state.load()
                        sid2 = state2.get("session_id") or ""
                        if sid2 and isinstance(val_ref, dict) and val_ref.get("path"):
                            from agent_system.runtime.review_store import ReviewArtifactStore as _StoreV
                            store_v = _StoreV(self.root, sid2)
                            vsnap_obj = store_v.load_validation(val_ref)
                        elif isinstance(val_ref, dict) and val_ref.get("status"):
                            vsnap_obj = val_ref
                    except Exception:
                        vsnap_obj = None
                        val_ref = None
                    # If existing validation is FAILED, trigger review retry instead of hard FAIL
                    if vsnap_obj is not None and vsnap_obj.get("status") != "PASSED":
                        attempt = int(delivery.get("review_attempt", 1))
                        reason = f"validation failed for {active.id}: {vsnap_obj.get('summary','')[:500]}"
                        if attempt >= MAX_REVIEW_ATTEMPTS:
                            return AgentResult(status="FAILED", message=reason, artifacts=[])
                        print(f"  Validation FAILED for {active.id}, retrying execution (attempt {attempt} -> {attempt+1})")
                        ckpt.set_phase(TaskPhase.EXECUTING, review_attempt=attempt + 1, last_review_reason=reason, candidate_ref=None, validation_ref=None, review_package_ref=None, reviewed_tree_sha=None, base_commit_sha=None, review_decision=None)
                        continue
                    if vsnap_obj is not None and vsnap_obj.get("status") == "PASSED":
                        # Reuse passed validation; skip to building review package
                        pass
                    else:
                        vsnap_obj = None
                        val_ref = None
                if vsnap_obj is None and active.validation:
                    from agent_system.runtime.validation import get_validation_runner
                    runner = get_validation_runner(self.root)
                    before_tree = self.git.snapshot_worktree_tree() or ""
                    vres = runner.validate(active, reviewed_tree or before_tree)
                    after_tree = self.git.snapshot_worktree_tree() or ""
                    if after_tree != (reviewed_tree or before_tree):
                        return AgentResult(status="FAILED", message="Validation mutated project tree; failing closed.", artifacts=[])
                    vsnap_obj = {"task_id": active.id, "tree_sha": vres.tree_sha or reviewed_tree or before_tree, "status": vres.status, "checks": [{"instruction": c.instruction, "status": c.status, "evidence": c.evidence[:4000]} for c in vres.checks], "summary": vres.summary, "tool_events": vres.tool_events}
                    if sid:
                        from agent_system.runtime.review_store import ReviewArtifactStore
                        store = ReviewArtifactStore(self.root, sid)
                        attempt2 = int(candidate.get("review_attempt", 1) or 1)
                        val_ref = store.write_validation(active.id, attempt2, vsnap_obj)
                    else:
                        val_ref = {"path": "", "sha256": hashlib.sha256(json.dumps(vsnap_obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
                    ckpt.update_delivery(validation_ref=val_ref)
                    if vres.status != "PASSED":
                        attempt = int(delivery.get("review_attempt", 1))
                        reason = f"validation failed for {active.id}: {vres.summary[:500]}"
                        if attempt >= MAX_REVIEW_ATTEMPTS:
                            return AgentResult(status="FAILED", message=reason, artifacts=[])
                        print(f"  Validation FAILED for {active.id}, retrying execution (attempt {attempt} -> {attempt+1})")
                        ckpt.set_phase(TaskPhase.EXECUTING, review_attempt=attempt + 1, last_review_reason=reason, candidate_ref=None, validation_ref=None, review_package_ref=None, reviewed_tree_sha=None, base_commit_sha=None, review_decision=None)
                        continue
                # Handle verification no-commit path
                if active.role == "test" and active.type == "verification":
                    if vsnap_obj is None or vsnap_obj.get("status") != "PASSED":
                        return AgentResult(status="FAILED", message="verification validation failed", artifacts=[])
                    if cur_snap.has_changes:
                        return AgentResult(status="FAILED", message="verification task has pending changes", artifacts=cur_snap.changed_files)
                    relevant_paths = self._relevant_files_union(active, original, cur_snap, candidate.get("result_artifacts", []))
                    try:
                        relevant_files = self._build_relevant_file_evidence(reviewed_tree, relevant_paths)
                    except RuntimeError as e:
                        return AgentResult(status="FAILED", message=str(e), artifacts=[])
                    pkg_obj = {"package_version": 1, "session_id": sid, "task_id": active.id, "original_task_id": original.id, "review_attempt": int(candidate.get("review_attempt", 1) or 1), "reviewed_tree_sha": reviewed_tree, "base_commit_sha": base_sha or "", "base_tree_sha": candidate.get("base_tree_sha", "") or "", "changed_files": cur_snap.changed_files, "project_diff": cur_snap.diff, "relevant_files": relevant_files, "candidate": candidate, "validation": vsnap_obj, "task_baseline": delivery.get("task_baseline")}
                    if sid:
                        from agent_system.runtime.review_store import ReviewArtifactStore as _Store2
                        store = _Store2(self.root, sid)
                        pkg_ref = store.write_review_package(active.id, int(candidate.get("review_attempt", 1) or 1), pkg_obj)
                    else:
                        pkg_ref = {"path": "", "sha256": hashlib.sha256(json.dumps(pkg_obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
                    ckpt.enter_reviewing(review_package_ref=pkg_ref, reviewed_tree_sha=reviewed_tree, base_commit_sha=base_sha or "")
                    continue
                # Normal code path: build ReviewPackage
                relevant_paths = self._relevant_files_union(active, original, cur_snap, candidate.get("result_artifacts", []))
                try:
                    relevant_files = self._build_relevant_file_evidence(reviewed_tree, relevant_paths)
                except RuntimeError as e:
                    return AgentResult(status="FAILED", message=str(e), artifacts=[])
                pkg_obj = {"package_version": 1, "session_id": sid, "task_id": active.id, "original_task_id": original.id, "review_attempt": int(candidate.get("review_attempt", 1) or 1), "reviewed_tree_sha": reviewed_tree, "base_commit_sha": base_sha or "", "base_tree_sha": candidate.get("base_tree_sha", "") or "", "changed_files": cur_snap.changed_files, "project_diff": cur_snap.diff, "relevant_files": relevant_files, "candidate": candidate, "validation": vsnap_obj, "task_baseline": delivery.get("task_baseline")}
                if sid:
                    from agent_system.runtime.review_store import ReviewArtifactStore
                    store = ReviewArtifactStore(self.root, sid)
                    pkg_ref = store.write_review_package(active.id, int(candidate.get("review_attempt", 1) or 1), pkg_obj)
                else:
                    pkg_ref = {"path": "", "sha256": hashlib.sha256(json.dumps(pkg_obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
                ckpt.enter_reviewing(review_package_ref=pkg_ref, reviewed_tree_sha=reviewed_tree, base_commit_sha=base_sha or "")
                continue
            if phase == TaskPhase.REVIEWING.value:
                pkg_ref = delivery.get("review_package_ref")
                if not pkg_ref:
                    return AgentResult(status="FAILED", message="REVIEWING requires review_package_ref", artifacts=[])
                try:
                    state = ckpt.state.load()
                    sid = state.get("session_id") or ""
                    if sid and isinstance(pkg_ref, dict) and pkg_ref.get("path"):
                        from agent_system.runtime.review_store import ReviewArtifactStore
                        store = ReviewArtifactStore(self.root, sid)
                        pkg = store.load_review_package(pkg_ref)
                    else:
                        pkg = pkg_ref if isinstance(pkg_ref, dict) and "reviewed_tree_sha" in pkg_ref else {}
                except Exception as e:
                    return AgentResult(status="FAILED", message=f"review package verification failed: {e}", artifacts=[])
                if not isinstance(pkg, dict) or not pkg.get("reviewed_tree_sha"):
                    return AgentResult(status="FAILED", message="review package invalid", artifacts=[])
                if pkg.get("reviewed_tree_sha") != delivery.get("reviewed_tree_sha"):
                    return AgentResult(status="FAILED", message="review package tree mismatch", artifacts=[])
                cur_snap = self.git.capture_tree_snapshot()
                if cur_snap.tree_sha != pkg.get("reviewed_tree_sha"):
                    return AgentResult(status="FAILED", message="Reviewed tree no longer matches workspace.", artifacts=[])
                if active.type == "optional":
                    if not pkg.get("project_diff", "").strip() and not pkg.get("changed_files"):
                        if not cur_snap.has_changes:
                            ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="SKIPPED", commit_sha=None, push_status="SKIPPED", ci_status="SKIPPED")
                            return AgentResult(status="SUCCESS", message=f"task {active.id} skipped", artifacts=[])
                if active.role == "test" and active.type == "verification":
                    validation = pkg.get("validation")
                    if validation and validation.get("status") == "PASSED":
                        if cur_snap.has_changes:
                            return AgentResult(status="FAILED", message="verification task has pending changes", artifacts=cur_snap.changed_files)
                        ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="VERIFIED", commit_sha=None, push_status="SKIPPED", ci_status="SKIPPED")
                        return AgentResult(status="SUCCESS", message=f"task {active.id} verified", artifacts=[])
                    return AgentResult(status="FAILED", message="verification validation failed", artifacts=[])
                review = self.tech_lead.review_package(active, pkg, ctx or project_context)
                if review.status == "SUCCESS":
                    if getattr(review, "outcome", None) and review.outcome.status == "SATISFIED":
                        changes_now = self.git.capture_tree_snapshot()
                        if changes_now.has_changes:
                            return AgentResult(status="FAILED", message=f"Runtime invariant violation: SATISFIED task has pending project changes: {changes_now.changed_files}", artifacts=changes_now.changed_files)
                        ckpt.update_delivery(review_decision={"review_package_sha256": pkg_ref.get("sha256", ""), "reviewed_tree_sha": pkg.get("reviewed_tree_sha"), "decision": "ALREADY_SATISFIED", "reason": review.message})
                        ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome="SATISFIED", commit_sha=None, push_status="SKIPPED", ci_status="SKIPPED")
                        return AgentResult(status="SUCCESS", message=review.message, artifacts=review.artifacts)
                    if getattr(review, "outcome", None) and review.outcome.status in ("VERIFIED", "SKIPPED"):
                        out = review.outcome.status
                        changes_now = self.git.capture_tree_snapshot()
                        if changes_now.has_changes:
                            return AgentResult(status="FAILED", message=f"{out} task has pending changes", artifacts=changes_now.changed_files)
                        ckpt.update_delivery(review_decision={"review_package_sha256": pkg_ref.get("sha256", ""), "reviewed_tree_sha": pkg.get("reviewed_tree_sha"), "decision": out, "reason": review.message})
                        ckpt.mark_task_completed(task_index=task_index, task_id=original.id, outcome=out, commit_sha=None, push_status="SKIPPED", ci_status="SKIPPED")
                        return AgentResult(status="SUCCESS", message=review.message, artifacts=review.artifacts)
                    if not review.commit_message:
                        return AgentResult(status="FAILED", message="Approved changed task has no commit message", artifacts=[])
                    snap2 = self.git.capture_tree_snapshot()
                    if snap2.tree_sha != pkg.get("reviewed_tree_sha"):
                        return AgentResult(status="FAILED", message="Reviewed tree no longer matches workspace.", artifacts=[])
                    head_ref = self.git.head_ref()
                    if not head_ref:
                        return AgentResult(status="FAILED", message="detached HEAD not supported", artifacts=[])
                    commit_intent = {"tree_sha": snap2.tree_sha, "parent_sha": snap2.base_commit_sha, "message": review.commit_message, "head_ref": head_ref, "pending_commit_sha": None, "review_package_sha256": pkg_ref.get("sha256", "")}
                    ckpt.update_delivery(review_decision={"review_package_sha256": pkg_ref.get("sha256", ""), "reviewed_tree_sha": pkg.get("reviewed_tree_sha"), "decision": "APPROVED", "reason": review.message, "commit_message": review.commit_message})
                    ckpt.enter_committing(pending_commit_message=review.commit_message, pre_commit_sha=snap2.base_commit_sha, commit_intent=commit_intent)
                    continue
                attempt = int(delivery.get("review_attempt", 1))
                if attempt >= MAX_REVIEW_ATTEMPTS:
                    return review
                ckpt.update_delivery(review_decision={"review_package_sha256": pkg_ref.get("sha256", ""), "reviewed_tree_sha": pkg.get("reviewed_tree_sha"), "decision": "CHANGES_REQUIRED", "reason": review.message})
                ckpt.set_phase(TaskPhase.EXECUTING, review_attempt=attempt + 1, last_review_reason=review.message, candidate_ref=None, validation_ref=None, review_package_ref=None, reviewed_tree_sha=None, base_commit_sha=None, review_decision=None)
                continue
            if phase == TaskPhase.COMMITTING.value:
                commit_intent = delivery.get("commit_intent") or {}
                review_decision = delivery.get("review_decision") or {}
                if review_decision.get("review_package_sha256") and commit_intent.get("review_package_sha256") and review_decision.get("review_package_sha256") != commit_intent.get("review_package_sha256"):
                    return AgentResult(status="FAILED", message="commit provenance mismatch", artifacts=[])
                pkg_ref = delivery.get("review_package_ref")
                if pkg_ref:
                    try:
                        state = ckpt.state.load()
                        sid = state.get("session_id") or ""
                        if sid and isinstance(pkg_ref, dict) and pkg_ref.get("path"):
                            from agent_system.runtime.review_store import ReviewArtifactStore
                            store = ReviewArtifactStore(self.root, sid)
                            pkg = store.load_review_package(pkg_ref)
                            if pkg.get("reviewed_tree_sha") != commit_intent.get("tree_sha"):
                                return AgentResult(status="FAILED", message="commit provenance tree mismatch", artifacts=[])
                    except Exception as e:
                        return AgentResult(status="FAILED", message=f"commit provenance verification failed: {e}", artifacts=[])
                pending = delivery.get("pending_commit_message", "") or commit_intent.get("message", "")
                pre_sha = delivery.get("pre_commit_sha") or commit_intent.get("parent_sha", "")
                head_ref = commit_intent.get("head_ref") or self.git.head_ref()
                tree_sha = commit_intent.get("tree_sha") or ""
                pending_sha = commit_intent.get("pending_commit_sha") or ""
                cur_sha = self.git.head_sha()
                if pending_sha and cur_sha == pending_sha:
                    ct = self.git.commit_tree_sha(pending_sha)
                    if ct != tree_sha:
                        return AgentResult(status="FAILED", message="commit tree mismatch on recovery", artifacts=[])
                    self.git.sync_index_to_head()
                    ckpt.update_delivery(commit_sha=pending_sha)
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
                    commit_intent = {"tree_sha": tree_sha, "parent_sha": pre_sha, "message": pending, "head_ref": head_ref, "pending_commit_sha": None, "review_package_sha256": delivery.get("review_decision", {}).get("review_package_sha256", "")}
                    ckpt.update_delivery(commit_intent=commit_intent, pre_commit_sha=pre_sha)
                if not pending_sha:
                    pending_sha = self.git.create_commit_object(tree_sha, pre_sha, pending)
                    if not pending_sha:
                        return AgentResult(status="FAILED", message=f"commit failed for {original.id}: commit-tree failed", artifacts=[])
                    ct = self.git.commit_tree_sha(pending_sha)
                    if ct != tree_sha:
                        return AgentResult(status="FAILED", message="commit tree mismatch", artifacts=[])
                    commit_intent["pending_commit_sha"] = pending_sha
                    ckpt.update_delivery(commit_intent=commit_intent, pending_commit_sha=pending_sha)
                ok = self.git.update_ref(head_ref, pending_sha, pre_sha) if pre_sha else self.git.update_ref(head_ref, pending_sha)
                if not ok:
                    return AgentResult(status="FAILED", message="update-ref failed (branch changed externally?)", artifacts=[])
                self.git.sync_index_to_head()
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
                corr_val = list(corr.get("validation", []) or [])
                orig_val = list(active.validation or [])
                merged = list(dict.fromkeys(orig_val + corr_val))
                if not merged:
                    merged = ["Run the repository test suite and confirm it passes.", "Verify the correction change is present and no unrelated files are modified."]
                corr_task = {"id": f"{original.id}-correction-{next_attempt}", "role": corr.get("role") or active.role, "type": corr.get("type") or active.type, "description": str(corr.get("description", "")).strip(), "acceptance": list(corr.get("acceptance", [])) or list(active.acceptance or []), "validation": merged, "files": list(corr.get("files", [])) or list(active.files or []), "source_commit_sha": sha}
                ckpt.save_correction_task(corr_task, attempt=next_attempt)
                ckpt.set_phase(TaskPhase.CORRECTING)
                continue
            if phase == TaskPhase.CORRECTING.value:
                ct = delivery.get("correction_task") or {}
                ckpt.set_phase(TaskPhase.EXECUTING, event="enter_executing", active_task_id=ct.get("id"), review_attempt=1, last_review_reason="", candidate_ref=None, validation_ref=None, review_package_ref=None, reviewed_tree_sha=None, base_commit_sha=None, review_decision=None)
                continue
            return AgentResult(status="FAILED", message=f"unknown phase: {phase}", artifacts=[])

    def _ci_discover(self, sha):
        from agent_system.runtime.ci_monitor import CIMonitor
        return CIMonitor(self.root).discover_for_commit(sha)
    def _ci_wait(self, runs):
        from agent_system.runtime.ci_monitor import CIMonitor
        return CIMonitor(self.root).wait_for_runs(runs)
