from pathlib import Path
from typing import Optional

from agent_system.agents.models import AgentResult, AgentTask
from agent_system.agents.parent import ParentAgent
from agent_system.config import resolve_api_key, resolve_base_url, resolve_model
from agent_system.context import ProjectContext, load_context
from agent_system.plan_parser import load_plan, parse_plan, parse_plan_json, render_plan_md
from agent_system.runtime.git import Git


MAX_REVIEW_ATTEMPTS = 3
MAX_CI_CORRECTIONS = 3


def _load_prompt(name: str) -> str:
    base = Path(__file__).parent.parent / "prompts"
    candidates = [
        base / f"{name}.md",
        base / name / "system.md",
        base / name / "planning.md",
    ]
    parts = []
    for p in candidates:
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    if parts:
        common = base / "common" / "engineering_rules.md"
        if common.exists():
            parts.append(common.read_text(encoding="utf-8"))
        return "\n\n".join(parts)
    return ""


class ClaudeParentAgent(ParentAgent):
    def __init__(self, root: Path = None, model: str = None):
        self.root = root or Path.cwd()
        self.model = model or resolve_model()
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

    def _run_task_state_machine(self, original: AgentTask, task_index: int, resume_current: bool = False) -> AgentResult:
        from agent_system.agents.code_agent import CodeAgent
        from agent_system.agents.subagent import MockTestAgent
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
                    from agent_system.runtime.coding import capture_task_baseline
                    baseline = capture_task_baseline(self.root, active)
                    ckpt.update_delivery(task_baseline=task_baseline_to_dict(baseline))
                review_attempt = int(delivery.get("review_attempt", 1))
                last_reason = delivery.get("last_review_reason", "")
                if active.role == "code" and review_attempt > 1 and last_reason:
                    exec_task = AgentTask(id=active.id, role=active.role, description=active.description + f"\n[Retry {review_attempt}: previous review failed: {last_reason}]", files=active.files, type=active.type, required=active.required, acceptance=active.acceptance, validation=active.validation)
                else:
                    exec_task = active
                if exec_task.role == "code":
                    result = CodeAgent(root=self.root, model=self.model).execute(exec_task, baseline=baseline)
                else:
                    result = MockTestAgent().execute(exec_task, baseline=baseline)
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
                review = self._review(active, tmp_result, project_diff)
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
                decision = self.ci_review(ci_status="CI_FAILED", ci_logs=ci_logs, commit_sha=sha, task=active)
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

    def run(self, task: str) -> AgentResult:
        print("Starting Claude Parent")
        ctx = load_context(self.root)
        print("  Loading context")
        if ctx.task:
            print("  TASK analyzed")
        if ctx.instructions:
            print("  CLAUDE.md loaded")
        if ctx.milestones:
            print("  milestones loaded")

        plan_json_file = self.root / ".agent" / "plan.json"
        plan_file = self.root / ".agent" / "plan.md"
        try:
            from agent_system.supervisor.state import StateManager as _SM2

            _mode = _SM2(self.root).load().get("execution_mode")
            _is_resume = _mode == "RESUME"
        except Exception:
            _is_resume = False
        if _is_resume and plan_file.exists() and plan_file.read_text(encoding="utf-8").strip():
            plan_text = plan_file.read_text(encoding="utf-8")
            print("  plan.md reused (resume)")
            plan_data, tasks = load_plan(self.root)
            if not tasks:
                tasks = parse_plan(plan_text)
        else:
            raw = self._plan(ctx.task or task, ctx)
            plan_data = None
            try:
                import json as _json

                s = raw.find("{")
                e = raw.rfind("}")
                if s >= 0 and e > s:
                    cand = _json.loads(raw[s:e+1])
                    if isinstance(cand.get("tasks"), list):
                        plan_data = cand
            except Exception:
                plan_data = None
            if plan_data is not None:
                plan_json_file.parent.mkdir(parents=True, exist_ok=True)
                import json as _json2

                plan_json_file.write_text(_json2.dumps(plan_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                plan_text = render_plan_md(plan_data)
                plan_file.parent.mkdir(parents=True, exist_ok=True)
                plan_file.write_text(plan_text, encoding="utf-8")
                print("  plan.json created")
                print("  plan.md rendered")
                tasks = parse_plan_json(plan_data)
                if not tasks:
                    print("  Planning FAILED: structured plan contains no valid executable tasks")
                    return AgentResult(status="FAILED", message="structured plan contains no valid executable tasks", artifacts=[])
            else:
                plan_text = raw
                plan_file.parent.mkdir(parents=True, exist_ok=True)
                plan_file.write_text(plan_text, encoding="utf-8")
                print("  plan.md created")
                tasks = parse_plan(plan_text)
        if not tasks:
            print("  Planning FAILED: no executable tasks produced")
            return AgentResult(status="FAILED", message="planning produced no executable tasks", artifacts=[])
        print(f"  parsed {len(tasks)} tasks from plan")
        for t in tasks:
            print(f"    {t.id} [{t.role}] {t.description}")

        from agent_system.supervisor.state import StateManager as _SM
        from agent_system.runtime.checkpoint import TaskPhase

        _st = _SM(self.root).load()
        is_resume = _st.get("execution_mode") == "RESUME"
        delivery = _st.get("delivery") or {}
        start_idx = 0
        if is_resume:
            completed = delivery.get("completed_task_index")
            current = delivery.get("current_task_index")
            if delivery.get("phase") == TaskPhase.TASK_COMPLETED.value and isinstance(completed, int):
                start_idx = completed + 1
            elif isinstance(current, int):
                start_idx = current
            if start_idx < 0:
                start_idx = 0
            if start_idx >= len(tasks):
                print("  All tasks already completed, skipping execution")
                return AgentResult(status="SUCCESS", message="plan executed", artifacts=[str(plan_file)], next_action="DONE")

        if not self.git.is_workspace_repo():
            print(f"Cannot start workflow: workspace is not a Git repository [{self.root}]")
            return AgentResult(status="FAILED", message="workspace not a Git repository", artifacts=[])

        for task_index in range(start_idx, len(tasks)):
            original = tasks[task_index]
            resume_current = is_resume and task_index == start_idx and delivery.get("phase") not in (None, "", TaskPhase.TASK_COMPLETED.value)
            print(f"  Dispatch: {original.id} -> {original.role} (index {task_index})")
            res = self._run_task_state_machine(original, task_index, resume_current=resume_current)
            if res.status == "FAILED":
                return res

        print("Parent finished")
        return AgentResult(status="SUCCESS", message="plan executed", artifacts=[str(plan_file)], next_action="DONE")

    def get_context(self) -> ProjectContext:
        return load_context(self.root)

    def ci_review(self, ci_status: str, ci_logs: str = "", task=None, commit_sha: str = None) -> dict:
        p = Path(__file__).parent.parent / "prompts" / "parent" / "ci_review.md"
        sys_text = p.read_text(encoding="utf-8") if p.exists() else _load_prompt("parent/ci_review") or "You are the Tech Lead reviewing CI results."
        from agent_system.context import load_context

        ctx = load_context(self.root)
        from agent_system.runtime.git import Git

        git = Git(self.root)
        diff = git.commit_diff(commit_sha) if commit_sha else git.diff()
        if hasattr(task, 'description'):
            task_block = f"Executable task: {task.id}\nDescription: {task.description}\nAcceptance: {task.acceptance}\nValidation: {task.validation}\nFiles: {task.files}"
        else:
            task_block = str(task) if task else ""
        user = (
            f"Original TASK:\n{ctx.task}\n\n"
            f"Current executable task:\n{task_block}\n\n"
            f"Plan:\n{ctx.plan}\n\n"
            f"Commit: {commit_sha or ''}\n\n"
            f"Diff:\n{diff}\n\n"
            f"CI status: {ci_status}\n\nCI logs:\n{ci_logs}"
        )
        text = self._invoke(sys_text, user)
        try:
            import json

            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                decision = data.get("decision", "")
                if decision in ("APPROVED", "NO_CODE_CHANGE", "APPROVED_WITH_NOTE"):
                    return {"decision": "APPROVED_WITH_NOTE" if decision == "NO_CODE_CHANGE" else decision, "reason": data.get("reason", text[:800]), "classification": data.get("classification", ""), "correction": data.get("correction", "")}
                if decision == "CHANGES_REQUIRED":
                    corr = data.get("correction", {})
                    if isinstance(corr, dict) and corr:
                        return {"decision": "CHANGES_REQUIRED", "reason": data.get("reason", text[:800]), "correction": corr, "classification": data.get("classification", "CHANGE_RELATED")}
                    return {"decision": "CHANGES_REQUIRED", "reason": data.get("reason", text[:800]), "correction": {}, "classification": "CHANGE_RELATED"}
        except Exception:
            pass
        if "APPROVE" in text.upper() or "CI_APPROVED" in text.upper():
            return {"decision": "APPROVED", "reason": text[:800]}
        low = text.lower()
        if "existing_project" in low or "infrastructure" in low:
            return {"decision": "APPROVED_WITH_NOTE", "reason": text[:800], "classification": text[:300]}
        return {"decision": "CHANGES_REQUIRED", "reason": text[:800], "correction": {"description": text[:500]}}

    def create_milestone(self, feedback: str = None) -> str:
        from datetime import datetime, timezone

        from agent_system.milestone.context import collect_context

        ctx = collect_context(self.root, feedback=feedback)
        p = Path(__file__).parent.parent / "prompts" / "parent" / "historian.md"
        system = p.read_text(encoding="utf-8") if p.exists() else _load_prompt("parent/historian") or "You are the Engineering Historian. Create a milestone document."
        prev_text = "\n\n---\n\n".join(ctx.previous_milestones[-2:]) if ctx.previous_milestones else "(none)"
        user = (
            f"Task:\n{ctx.task}\n\n"
            f"Plan:\n{ctx.plan}\n\n"
            f"Diff:\n{ctx.diff}\n\n"
            f"Git log:\n{ctx.git_log}\n\n"
            f"Repo state:\n{ctx.repo_state}\n\n"
            f"Previous milestones (last 2):\n{prev_text}\n\n"
            f"Human feedback:\n{ctx.human_feedback or '(none)'}"
        )
        content = self._invoke(system, user)
        milestones_dir = self.root / ".agent" / "milestones"
        milestones_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(milestones_dir.glob("*.md"))
        next_id = len(existing) + 1
        fname = f"{next_id:03d}.md"
        out = milestones_dir / fname
        out.write_text(content if content.lstrip().startswith("#") else f"# Milestone {next_id:03d}\n\n{content}", encoding="utf-8")
        return str(out)

    def _quick_baseline_check(self, task, baseline, diff: str):
        if not baseline or not baseline.files or not task.files:
            return None
        try:
            all_new = all(not snap.exists for snap in baseline.files.values())
            if not all_new:
                return None
            if not any(rel in diff for rel in task.files):
                return None
            if len(diff.strip()) < 50:
                return None
            return {"preservation_risk": False, "reason": "all task files were absent at baseline; new-file preservation concern does not apply"}
        except Exception:
            return None

    def _format_baseline(self, baseline) -> str:
        if not baseline:
            return "(no baseline captured)"
        lines = [f"HEAD: {baseline.commit_sha[:7] if baseline.commit_sha else '(none)'}"]
        for rel, snap in (baseline.files or {}).items():
            if snap.exists:
                lines.append(f"{rel}: exists (baseline)")
            else:
                lines.append(f"{rel}: did not exist at task baseline (new file is expected)")
        if not baseline.files:
            lines.append("(no task files tracked)")
        return "\n".join(lines)

    def _format_evidence(self, evidence) -> str:
        if not evidence or not evidence.events:
            return "(no tool evidence)"
        lines = []
        for ev in evidence.events:
            lines.append(f"{ev.tool} {ev.input} -> exit {ev.exit_code}: {ev.output}")
        return "\n".join(lines)

    def _review(self, task, result: AgentResult, project_diff: str = "") -> AgentResult:
        if getattr(result, 'execution_status', 'COMPLETED') == "ERROR" or result.status == "FAILED":
            if getattr(result, 'execution_status', None) == "ERROR":
                return AgentResult(status="FAILED", message=f"task {task.id} runtime error: {result.message}", artifacts=result.artifacts, baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None), execution_status="ERROR", stop_reason=getattr(result, 'stop_reason', None))
            return AgentResult(status="FAILED", message=f"task {task.id} failed: {result.message}", artifacts=result.artifacts, baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))
        if result.status == "INCOMPLETE":
            return AgentResult(status="FAILED", message=f"task {task.id} incomplete: execution budget exhausted without producing expected changes", artifacts=result.artifacts, baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))
        if not result.message:
            return AgentResult(status="FAILED", message=f"task {task.id} produced empty result", artifacts=result.artifacts)
        if task.type == "verification":
            return AgentResult(status="SUCCESS", message=f"task {task.id} accepted (verification)", artifacts=result.artifacts)
        if task.type == "optional":
            return AgentResult(status="SUCCESS", message=f"task {task.id} accepted (optional)", artifacts=result.artifacts)
        if task.role != "code":
            return AgentResult(status="SUCCESS", message=f"task {task.id} accepted (non-code)", artifacts=result.artifacts)

        diff = (project_diff or "").strip()
        if not diff:
            sat = self._satisfaction_review(task, result)
            if sat is None:
                print(f"  Satisfaction review unavailable for {task.id} — failing closed")
                return AgentResult(status="FAILED", message=f"task {task.id} satisfaction review unavailable", artifacts=result.artifacts, baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))
            if sat.get("decision") == "ALREADY_SATISFIED":
                from agent_system.agents.models import TaskOutcome

                return AgentResult(status="SUCCESS", message=f"task {task.id} already satisfied: {sat.get('reason','')}", artifacts=result.artifacts, outcome=TaskOutcome(task_id=task.id, status="SATISFIED", decision="ALREADY_SATISFIED", reason=sat.get("reason",""), evidence=sat.get("evidence",[])), baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))
            if sat.get("decision") == "CHANGES_REQUIRED":
                return AgentResult(status="FAILED", message=f"task {task.id} not yet satisfied: {sat.get('reason','')}", artifacts=result.artifacts, baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))
            return AgentResult(status="FAILED", message=f"task {task.id} satisfaction review error", artifacts=result.artifacts, baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))
        ctx = load_context(self.root)
        baseline_obj = getattr(result, 'baseline', None)
        evidence_obj = getattr(result, 'evidence', None)
        baseline_text = self._format_baseline(baseline_obj)
        evidence_text = self._format_evidence(evidence_obj)
        quick = self._quick_baseline_check(task, baseline_obj, diff)
        if quick is not None:
            print(f"    baseline advisory: {quick['reason']}")
            baseline_text += f"\nAdvisory: {quick['reason']}"
        score = self._llm_review(task, result, diff, ctx, baseline_text=baseline_text, evidence_text=evidence_text)
        if score is None:
            print(f"  Review ERROR for {task.id}: reviewer unavailable or invalid response — failing closed")
            return AgentResult(status="FAILED", message=f"task {task.id} review error: reviewer unavailable", artifacts=result.artifacts, baseline=baseline_obj, evidence=evidence_obj)
        if not score.get("pass", True):
            return AgentResult(status="FAILED", message=f"task {task.id} review failed: {score.get('reason','')}", artifacts=result.artifacts, baseline=baseline_obj, evidence=evidence_obj)
        if diff.strip():
            print(f"    diff: {diff[:200]}")
        cm = self._commit_message(task, diff)
        from agent_system.agents.models import TaskOutcome as _TO2

        return AgentResult(status="SUCCESS", message=f"task {task.id} accepted", artifacts=result.artifacts, commit_message=cm, outcome=_TO2(task_id=task.id, status="CHANGED", decision="APPROVED"), baseline=getattr(result, 'baseline', None), evidence=getattr(result, 'evidence', None))

    def _satisfaction_review(self, task, result):
        try:
            import anthropic, json

            from agent_system.config import resolve_api_key, resolve_base_url

            api_key = resolve_api_key()
            if not api_key:
                return None
            base_url = resolve_base_url()
            client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=15) if base_url else anthropic.Anthropic(api_key=api_key, timeout=15)
            p = Path(__file__).parent.parent / "prompts" / "review" / "system.md"
            system = p.read_text(encoding="utf-8") if p.exists() else "You are a reviewer. Decide if the repository already satisfies the task. Return JSON: {\"decision\": \"ALREADY_SATISFIED\"|\"CHANGES_REQUIRED\", \"reason\": string}"
            ctx = load_context(self.root)
            import pathlib

            baseline_text = self._format_baseline(getattr(result, 'baseline', None))
            evidence_text = self._format_evidence(getattr(result, 'evidence', None))
            candidates = list(dict.fromkeys(list(task.files or []) + list(getattr(result, "artifacts", None) or [])))
            repo_evidence = ""
            for rel in candidates:
                pp = self.root / rel
                if pp.is_file():
                    try:
                        repo_evidence += f"\n--- {rel} ---\n{pp.read_text(encoding='utf-8')}\n"
                    except Exception:
                        repo_evidence += f"\n--- {rel} --- (unreadable)\n"
                else:
                    repo_evidence += f"\n--- {rel} --- (not found)\n"
            try:
                r = self.git.shell.run("git ls-files")
                listing = r.stdout.strip() if r.returncode == 0 else ""
            except Exception:
                listing = ""
            repo_evidence += f"\n--- git ls-files ---\n{listing}\n" if listing else ""
            if task.acceptance:
                repo_evidence += "\nAcceptance:\n" + "\n".join(f"- {a}" for a in task.acceptance)
            user = f"Task: {task.description}\nBaseline:\n{baseline_text}\nRepo evidence:\n{repo_evidence}\nTool evidence:\n{evidence_text}\nPlan:\n{ctx.plan}\nResult:\n{result.message}\nDiff is empty — decide if the repository already satisfies all acceptance criteria."
            resp = client.messages.create(model=self.model or "claude-sonnet-4-20250514", max_tokens=512, system=system, messages=[{"role": "user", "content": user}])
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            s = text.find("{")
            e = text.rfind("}") + 1
            if s >= 0 and e > s:
                data = json.loads(text[s:e])
                dec = data.get("decision", "")
                if dec in ("ALREADY_SATISFIED", "SATISFIED", "APPROVED", "NO_CHANGE"):
                    return {"decision": "ALREADY_SATISFIED", "reason": data.get("reason", text[:500]), "evidence": data.get("evidence", [])}
                if dec in ("CHANGES_REQUIRED", "REQUIRED"):
                    return {"decision": "CHANGES_REQUIRED", "reason": data.get("reason", text[:500]), "correction": data.get("correction", "")}
        except Exception:
            return None
        return None

    def generate_commit_message(self, diff: str, hint: str = "") -> str:
        p = Path(__file__).parent.parent / "prompts" / "parent" / "commit_message.md"
        system = p.read_text(encoding="utf-8") if p.exists() else _load_prompt("parent/commit_message") or "Generate a commit message."
        user = f"Diff:\n{diff}\n\nHint:\n{hint}" if hint else f"Diff:\n{diff}"
        text = self._invoke(system, user)
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        if lines:
            msg = lines[0].strip('"').strip("'").strip()
            if msg:
                return msg
        return "chore: preserve existing changes"

    def _commit_message(self, task, diff: str) -> str:
        return self.generate_commit_message(diff, hint=task.description)

    def _llm_review(self, task, result: AgentResult, diff: str, ctx: ProjectContext, baseline_text: str = "", evidence_text: str = ""):
        try:
            import anthropic, json

            api_key = resolve_api_key()
            if not api_key:
                return None
            base_url = resolve_base_url()
            client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=15) if base_url else anthropic.Anthropic(api_key=api_key, timeout=15)
            p = Path(__file__).parent.parent / "prompts" / "review" / "system.md"
            system = p.read_text(encoding="utf-8") if p.exists() else "You are a code reviewer. Reply JSON only: {\"decision\": \"APPROVED\"|\"CHANGES_REQUIRED\", \"reason\": string}"
            val = "\n".join(f"- {v}" for v in (task.validation or [])) or "(none)"
            acc = "\n".join(f"- {a}" for a in (task.acceptance or [])) or "(none)"
            baseline_text = baseline_text or self._format_baseline(getattr(result, 'baseline', None))
            evidence_text = evidence_text or self._format_evidence(getattr(result, 'evidence', None))
            user = f"Task: {task.description}\nAcceptance:\n{acc}\nValidation:\n{val}\nBaseline:\n{baseline_text}\nEvidence:\n{evidence_text}\nPlan:\n{ctx.plan}\nDiff:\n{diff}\nResult:\n{result.message}"
            resp = client.messages.create(
                model=self.model or "claude-sonnet-4-20250514",
                max_tokens=512,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                dec = str(data.get("decision", "")).upper()
                if dec in ("APPROVED", "ALREADY_SATISFIED", "SATISFIED"):
                    return {"pass": True, "reason": data.get("reason", ""), "decision": dec}
                if dec in ("CHANGES_REQUIRED", "REJECTED", "FAILED"):
                    return {"pass": False, "reason": data.get("reason", text[:500]), "correction": data.get("correction", ""), "decision": dec}
                if "pass" in data:
                    return data
        except Exception:
            return None
        return None

    def _plan(self, task: str, ctx: ProjectContext) -> str:
        base = Path(__file__).parent.parent / "prompts"
        planning_path = base / "parent" / "planning.md"
        system = planning_path.read_text(encoding="utf-8") if planning_path.exists() else _load_prompt("parent")
        if (base / "common" / "engineering_rules.md").exists():
            system = system + "\n\n" + (base / "common" / "engineering_rules.md").read_text(encoding="utf-8")
        milestones_text = "\n\n".join(f"## {m.name}\n{m.content}" for m in ctx.milestones)
        user = f"Task:\n{task}\n\nRepo: {ctx.repository}\n\nCLAUDE.md:\n{ctx.instructions}\n\nMilestones:\n{milestones_text}"
        invoked = self._invoke(system, user)
        if invoked and not invoked.startswith("mock result"):
            s = invoked.find("{")
            e = invoked.rfind("}")
            if s >= 0 and e > s:
                try:
                    import json as _pj

                    from agent_system.plan_parser import is_valid_plan_data as _valid

                    cand = _pj.loads(invoked[s:e+1])
                    if _valid(cand):
                        return _pj.dumps(cand, ensure_ascii=False)
                except Exception:
                    pass
            print("  [plan: invalid structured response, preserving original task as fallback]")
        def _first_meaningful_line(t: str) -> str:
            for line in t.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                return s
            return "Implement feature"
        first_meaningful = _first_meaningful_line(task)
        import json as _jf

        import re as _re2

        _files_hint = list(dict.fromkeys(_re2.findall(r"[\w./-]+\.[A-Za-z0-9]+", task)))

        def _extract_markdown_list_section(text: str, headings: set) -> list:
            norm_headings = {h.lower().rstrip(":").strip() for h in headings}
            all_known = {"acceptance", "acceptance criteria", "validation"}
            lines = text.splitlines()
            result: list[str] = []
            in_section = False
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                h = stripped.lstrip("#").strip().rstrip(":").lower()
                if h in norm_headings:
                    in_section = True
                    continue
                if in_section:
                    if re.match(r"^#{1,6}\s+", stripped):
                        break
                    nh = stripped.lstrip("#").strip().rstrip(":").lower()
                    if nh in norm_headings:
                        break
                    if nh in all_known and nh not in norm_headings:
                        break
                    m = re.match(r"^[-*]\s+(.*)", stripped)
                    if m:
                        item = m.group(1).strip()
                        if item:
                            result.append(item)
                        continue
                    m2 = re.match(r"^\d+[\.\)]\s+(.*)", stripped)
                    if m2:
                        item = m2.group(1).strip()
                        if item:
                            result.append(item)
                        continue
                    if re.match(r"^-{2,}$", stripped) or re.match(r"^={2,}$", stripped):
                        break
                    m3 = re.match(r"^[-*]\s*$", stripped)
                    if m3:
                        continue
            return result

        _acceptance = _extract_markdown_list_section(task, {"acceptance", "acceptance criteria"})
        _validation = _extract_markdown_list_section(task, {"validation"})

        acceptance = _acceptance if _acceptance else ["Satisfy all requirements described in the original executable task."]
        validation = _validation

        fallback = {
            "objective": first_meaningful,
            "analysis": "Structured planning output was unavailable. The original task is preserved as one complete executable delivery unit so requirements are not lost.",
            "tasks": [
                {
                    "id": "task001",
                    "role": "code",
                    "type": "implementation",
                    "description": task.strip(),
                    "acceptance": acceptance,
                    "validation": validation,
                    "files": _files_hint,
                }
            ],
            "risks": ["Planner structured output was unavailable; Runtime preserved the original task without semantic reduction."],
        }
        return _jf.dumps(fallback, ensure_ascii=False)

    def _execute(self, plan: str, ctx: ProjectContext):
        raise RuntimeError("_execute is removed — no executable tasks must fail, not mock-execute")

    def _invoke(self, system: str, user: str) -> str:
        try:
            import anthropic

            api_key = resolve_api_key()
            if not api_key:
                raise RuntimeError("API key not configured")

            base_url = resolve_base_url()
            if base_url:
                client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=20)
            else:
                client = anthropic.Anthropic(api_key=api_key, timeout=20)
            kwargs = dict(
                model=self.model or "claude-sonnet-4-20250514",
                max_tokens=2048,
                messages=[{"role": "user", "content": user}],
            )
            if system:
                kwargs["system"] = system
            resp = client.messages.create(**kwargs)
            parts = []
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    parts.append(block.text)
            return "\n".join(parts)
        except Exception as e:
            raise RuntimeError(f"Parent API error: {e}") from e

    def commit_message_from_mock(self) -> str:
        return "chore: preserve existing changes"
