from pathlib import Path

from agent_system.agents.models import AgentResult
from agent_system.agents.parent import ParentAgent
from agent_system.config import resolve_api_key, resolve_base_url, resolve_model
from agent_system.context import ProjectContext, load_context
from agent_system.plan_parser import load_plan, parse_plan, parse_plan_json, render_plan_md
from agent_system.runtime.git import Git


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
        if tasks:
            print(f"  parsed {len(tasks)} tasks from plan")
            for t in tasks:
                print(f"    {t.id} [{t.role}] {t.description}")

            from agent_system.agents.code_agent import CodeAgent
            from agent_system.agents.models import AgentTask
            from agent_system.agents.subagent import MockTestAgent
            from agent_system.supervisor.state import StateManager as _SM

            _st = _SM(self.root).load()
            is_resume = _st.get("execution_mode") == "RESUME"
            start_idx = 0
            delivery = _st.get("delivery", {}) if isinstance(_st.get("delivery"), dict) else {}
            phase = delivery.get("phase", "")
            c_idx = delivery.get("completed_task_index")
            cur_idx = delivery.get("current_task_index")
            if is_resume and isinstance(c_idx, int):
                start_idx = c_idx + 1
                if 0 < start_idx < len(tasks):
                    print(f"  Resuming from completed_task_index {c_idx} -> {start_idx}")
                elif start_idx >= len(tasks):
                    print("  All tasks already completed, skipping execution")
                    tasks = []
                else:
                    start_idx = 0
            elif is_resume and isinstance(cur_idx, int):
                if phase in ("TASK_COMPLETED", "CI_PASSED", "CI_NOT_DETECTED"):
                    start_idx = cur_idx + 1
                    print(f"  Resuming from current_task_index {cur_idx} phase {phase} -> {start_idx}")
                else:
                    start_idx = cur_idx
                    print(f"  Resuming current task {cur_idx} phase {phase}")
            elif is_resume and isinstance(delivery.get("task_index"), int):
                ti = delivery["task_index"]
                # Legacy narrow guard: only skip if that task truly completed
                if delivery.get("phase") == "TASK_COMPLETED" and 0 <= ti < len(tasks):
                    start_idx = ti + 1
                    print(f"  Resuming (legacy) from task_index {ti} -> {start_idx}")
            elif is_resume and delivery.get("task_id"):
                done_id = delivery["task_id"]
                ci_s = delivery.get("ci_status", "")
                if ci_s in ("CI_PASSED", "CI_NOT_DETECTED", "APPROVED_WITH_NOTE", "CI_NOT_CONFIGURED") and delivery.get("phase") == "TASK_COMPLETED":
                    for _idx, _t in enumerate(tasks):
                        if _t.id == done_id:
                            start_idx = _idx + 1
                            break
                    if start_idx > 0:
                        print(f"  Resuming from task_id {done_id} -> {start_idx}")
            tasks = tasks[start_idx:]

            # If resuming into CORRECTING, restore persisted correction task at the head
            _resume_delivery = _SM(self.root).load().get("delivery") or {}
            if _resume_delivery.get("phase") == "CORRECTING" and _resume_delivery.get("correction_task"):
                ct = _resume_delivery["correction_task"]
                tasks[0] = AgentTask(id=ct.get("id", tasks[0].id), role=ct.get("role", tasks[0].role), description=ct.get("description", tasks[0].description), files=ct.get("files", tasks[0].files), type=ct.get("type", tasks[0].type), required=tasks[0].required, acceptance=ct.get("acceptance", tasks[0].acceptance), validation=ct.get("validation", tasks[0].validation))
                print(f"  Resuming CORRECTING with persisted correction: {ct.get('description','')[:80]}")

            # Local workspaces must be Git repos even when not pushing
            if not self.git.is_workspace_repo():
                print(f"Cannot start workflow: workspace is not a Git repository [{self.root}]")
                return AgentResult(status="FAILED", message="workspace not a Git repository", artifacts=[])
            for task_index, t in enumerate(tasks, start=start_idx):
                print(f"  Dispatch: {t.id} -> {t.role}")
                from agent_system.supervisor.state import StateManager as _SM_PH

                _SM_PH(self.root).update(status="RUNNING", delivery={**_SM(self.root).load().get("delivery", {}), "current_task_index": task_index, "task_id": t.id, "phase": "EXECUTING"})
                last_diff = ""
                last_reason = ""
                for attempt in range(1, 4):
                    diff_before = self.git.diff()
                    if t.role == "code":
                        if attempt > 1:
                            t = AgentTask(id=t.id, role=t.role, description=t.description + f"\n[Retry {attempt}: previous review failed: {last_reason}]", files=t.files, type=t.type, required=t.required, acceptance=t.acceptance, validation=t.validation)
                        result = CodeAgent(root=self.root, model=self.model).execute(t)
                    else:
                        result = MockTestAgent().execute(t)

                    diff_after = self.git.diff()
                    review = self._review(t, result, diff_before, diff_after)
                    if review.status == "SUCCESS":
                        if getattr(review, "outcome", None) and review.outcome.status == "SATISFIED":
                            print(f"  Review SATISFIED for {t.id} (attempt {attempt}): {review.message}")
                            from agent_system.delivery import DeliveryConfig as _DC_SAT
                            from agent_system.supervisor.state import StateManager as _SM_SAT

                            _SM_SAT(self.root).update(status="RUNNING", delivery={"mode": _DC_SAT.load(self.root).mode, "task_id": t.id, "task_index": task_index, "current_task_index": task_index, "completed_task_index": task_index, "commit_sha": None, "outcome": "SATISFIED", "phase": "TASK_COMPLETED"})
                            break
                        print(f"  Review PASSED for {t.id} (attempt {attempt})")
                        if t.type == "implementation" and review.commit_message:
                            from agent_system.delivery import DeliveryConfig
                            from agent_system.runtime.delivery_runtime import DeliveryRuntime
                            from agent_system.supervisor.state import StateManager

                            cfg_pre = DeliveryConfig.load(self.root)
                            delivery = DeliveryRuntime(self.root)
                            msg = review.commit_message
                            if cfg_pre.mode == "local" and not self.git.is_workspace_repo():
                                print(f"  Local mode (no git): skipping commit: {msg}")
                                StateManager(self.root).update(status="RUNNING", delivery={"mode": "local", "task_id": t.id, "task_index": task_index, "current_task_index": task_index, "completed_task_index": task_index, "commit_sha": None, "phase": "TASK_COMPLETED"})
                                break
                            cres = delivery.commit(msg)
                            if cres.get("status") != "SUCCESS":
                                print(f"  Commit FAILED: {cres.get('message','')[:120]}")
                                return AgentResult(status="FAILED", message=f"commit failed for {t.id}: {cres.get('message','')[:200]}", artifacts=result.artifacts)
                            sha = cres.get("sha")
                            print(f"  Committed: {msg} [{sha[:7] if sha else ''}]")
                            cfg = DeliveryConfig.load(self.root)
                            if cfg.mode == "gh" and sha:
                                push_res = delivery.push(commit_sha=sha)
                                if push_res["status"] == "SUCCESS":
                                    print(f"  Pushed: {push_res['message'][:80]}")
                                elif push_res["status"] in ("REMOTE_FAILED", "NO_REMOTE"):
                                    label = "Push failed (fallback to local)" if push_res["status"] == "REMOTE_FAILED" else "No remote, skipping CI"
                                    print(f"  {label}: {push_res['message'][:80]}")
                                    StateManager(self.root).update(status="RUNNING", delivery={"mode": "local", "configured_mode": "gh", "push_status": push_res["status"], "task_id": t.id, "task_index": task_index, "current_task_index": task_index, "completed_task_index": task_index, "commit_sha": sha, "phase": "TASK_COMPLETED"})
                                    break
                                else:
                                    print(f"  Push skipped: {push_res['message'][:80]}")
                                    break
                                StateManager(self.root).update(status="WAITING_CI", delivery={"mode": "gh", "commit_sha": sha, "task_id": t.id, "task_index": task_index, "current_task_index": task_index, "phase": "WAITING_CI"})
                                print(f"  WAITING_CI for {sha[:7]}")
                                ci_res = delivery.wait_ci(sha)
                                ci_status = ci_res.get("status", "CI_NOT_DETECTED")
                                if ci_status == "CI_PASSED":
                                    StateManager(self.root).update(status="RUNNING", delivery={"mode": "gh", "commit_sha": sha, "ci_status": "CI_PASSED", "task_id": t.id, "task_index": task_index, "current_task_index": task_index, "completed_task_index": task_index, "phase": "TASK_COMPLETED"})
                                    print("  CI_PASSED")
                                elif ci_status == "CI_FAILED":
                                    print("  CI_FAILED")
                                    ci_decision = self.ci_review(ci_status="CI_FAILED", ci_logs=ci_res.get("failed_logs", ""), commit_sha=sha, task=t)
                                    if ci_decision["decision"] == "CHANGES_REQUIRED":
                                        corr_raw = ci_decision.get("correction", "")
                                        # Support structured correction dict or plain string
                                        if isinstance(corr_raw, dict) and corr_raw:
                                            c_desc = str(corr_raw.get("description", "")).strip() or t.description
                                            c_acc = list(corr_raw.get("acceptance", [])) or t.acceptance
                                            c_val = list(corr_raw.get("validation", [])) or t.validation
                                            c_files = list(corr_raw.get("files", [])) or t.files
                                        else:
                                            c_desc = str(corr_raw)[:500] or t.description
                                            c_acc = t.acceptance
                                            c_val = t.validation
                                            c_files = t.files
                                        corr_task = {"id": f"{t.id}-correction", "description": c_desc, "acceptance": c_acc, "validation": c_val, "files": c_files, "role": t.role, "type": t.type}
                                        attempt_no = int((StateManager(self.root).load().get("delivery") or {}).get("correction_attempt", 0)) + 1
                                        if attempt_no > 3:
                                            print(f"  CI correction limit reached ({attempt_no}) — failing")
                                            StateManager(self.root).update(status="FAILED", delivery={"phase": "CI_REVIEW", "correction_task": corr_task, "ci_status": "CI_FAILED"})
                                            return AgentResult(status="FAILED", message=f"CI correction limit exceeded for {t.id}", artifacts=result.artifacts)
                                        StateManager(self.root).update(status="RUNNING", delivery={"mode": "gh", "commit_sha": sha, "ci_status": "CI_FAILED", "task_id": t.id, "task_index": task_index, "current_task_index": task_index, "phase": "CORRECTING", "correction_task": corr_task, "correction_attempt": attempt_no})
                                        print(f"  CI correction needed: {c_desc[:80]}")
                                        t = AgentTask(id=corr_task["id"], role=corr_task["role"], description=corr_task["description"], files=corr_task["files"], type=corr_task["type"], required=t.required, acceptance=corr_task["acceptance"], validation=corr_task["validation"])
                                        last_reason = ci_decision["reason"]
                                        continue
                                    else:
                                        StateManager(self.root).update(status="RUNNING", delivery={"mode": "gh", "commit_sha": sha, "ci_status": ci_decision["decision"], "task_id": t.id, "task_index": task_index, "current_task_index": task_index, "completed_task_index": task_index, "phase": "TASK_COMPLETED"})
                                elif ci_status == "CI_NOT_DETECTED":
                                    print("  No CI runs detected, continuing")
                                    StateManager(self.root).update(status="RUNNING", delivery={"mode": "gh", "commit_sha": sha, "ci_status": "CI_NOT_DETECTED", "task_id": t.id, "task_index": task_index, "current_task_index": task_index, "completed_task_index": task_index, "phase": "TASK_COMPLETED"})
                                else:
                                    StateManager(self.root).update(status="RUNNING", delivery={"mode": "gh", "commit_sha": sha, "task_id": t.id, "task_index": task_index, "current_task_index": task_index, "completed_task_index": task_index, "phase": "TASK_COMPLETED"})
                            else:
                                dres = delivery.deliver(commit_sha=sha)
                                if dres.push_status == "SUCCESS":
                                    print(f"  Pushed: {dres.push_message[:80]}")
                                elif dres.push_status == "REMOTE_FAILED":
                                    print(f"  Push failed (fallback to local): {dres.push_message[:80]}")
                                elif dres.push_status == "NO_REMOTE":
                                    print("  Local mode: no remote")
                                elif dres.push_status == "SKIPPED":
                                    print("  Local delivery: committed")
                        break
                    if review.status == "FAILED" and "already satisfied" not in review.message.lower() and "not yet satisfied" not in review.message.lower():
                        cur_diff = diff_after[len(diff_before):] if diff_after.startswith(diff_before) else diff_after
                        if cur_diff.strip() == last_diff.strip() and attempt > 1:
                            print(f"  No new changes (same diff), stopping retry for {t.id}")
                            return review
                        last_diff = cur_diff
                    else:
                        raw_cur = diff_after[len(diff_before):] if diff_after.startswith(diff_before) else diff_after
                        filtered_cur = "\n".join(l for l in raw_cur.splitlines() if ".agent/" not in l and "__pycache__" not in l and ".pyc" not in l).strip()
                        last_diff = filtered_cur
                    last_reason = review.message
                    print(f"  Review FAILED for {t.id} (attempt {attempt}): {review.message}")
                    if attempt == 3:
                        return review
                    print(f"  Retrying {t.id}...")

        print("Parent finished")
        return AgentResult(
            status="SUCCESS",
            message="plan executed",
            artifacts=[str(plan_file)],
            next_action="DONE",
        )

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
            f"Original TASK:\n{ctx.task[:3000]}\n\n"
            f"Current executable task:\n{task_block[:3000]}\n\n"
            f"Plan:\n{ctx.plan[:3000]}\n\n"
            f"Commit: {commit_sha or ''}\n\n"
            f"Diff:\n{diff[:4000]}\n\n"
            f"CI status: {ci_status}\n\nCI logs:\n{ci_logs[:4000]}"
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
                        # Preserve structured correction as full AgentTask fields
                        return {"decision": "CHANGES_REQUIRED", "reason": data.get("reason", text[:800]), "correction": corr, "classification": data.get("classification", "CHANGE_RELATED")}
                    if isinstance(corr, dict):
                        desc = corr.get("description", "")
                        return {"decision": "CHANGES_REQUIRED", "reason": data.get("reason", text[:800]), "correction": desc or str(corr), "classification": "CHANGE_RELATED"}
                    return {"decision": "CHANGES_REQUIRED", "reason": data.get("reason", text[:800]), "correction": str(corr) if corr else text}
        except Exception:
            pass
        if "APPROVE" in text.upper() or "CI_APPROVED" in text.upper():
            return {"decision": "APPROVED", "reason": text[:800]}
        low = text.lower()
        if "existing_project" in low or "infrastructure" in low:
            return {"decision": "APPROVED_WITH_NOTE", "reason": text[:800], "classification": text[:300]}
        return {"decision": "CHANGES_REQUIRED", "reason": text[:800], "correction": text}

    def create_milestone(self, feedback: str = None) -> str:
        from datetime import datetime, timezone

        from agent_system.milestone.context import collect_context

        ctx = collect_context(self.root, feedback=feedback)
        p = Path(__file__).parent.parent / "prompts" / "parent" / "historian.md"
        system = p.read_text(encoding="utf-8") if p.exists() else _load_prompt("parent/historian") or "You are the Engineering Historian. Create a milestone document."
        prev_text = "\n\n---\n\n".join(ctx.previous_milestones[-2:]) if ctx.previous_milestones else "(none)"
        user = (
            f"Task:\n{ctx.task[:4000]}\n\n"
            f"Plan:\n{ctx.plan[:4000]}\n\n"
            f"Diff:\n{ctx.diff[:6000]}\n\n"
            f"Git log:\n{ctx.git_log[:2000]}\n\n"
            f"Repo state:\n{ctx.repo_state[:2000]}\n\n"
            f"Previous milestones (last 2):\n{prev_text[:4000]}\n\n"
            f"Human feedback:\n{ctx.human_feedback or '(none)'}"
        )
        content = self._invoke(system, user)
        if content.startswith("mock result"):
            content = (
                f"# Milestone {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
                f"## Objective\n{ctx.task[:200] or 'Completed task'}\n\n"
                f"## Implementation Summary\n{content}\n\n"
                f"## Architecture Decisions\nSee plan and diff.\n\n"
                f"## Challenges\nNone noted.\n\n"
                f"## Future Considerations\nContinue iteration.\n"
            )
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
        for ev in evidence.events[-20:]:
            lines.append(f"{ev.tool} {ev.input} -> exit {ev.exit_code}: {ev.output[:400]}")
        return "\n".join(lines)

    def _review(self, task, result: AgentResult, diff_before: str = "", diff_after: str = "") -> AgentResult:
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

        raw_diff = diff_after[len(diff_before):] if diff_after.startswith(diff_before) else diff_after
        filtered = "\n".join(l for l in raw_diff.splitlines() if ".agent/" not in l and "__pycache__" not in l and ".pyc" not in l)
        diff = filtered.strip()
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
            repo_evidence = ""
            for rel in (task.files or []):
                pp = self.root / rel
                if pp.is_file():
                    try:
                        repo_evidence += f"\n--- {rel} ---\n{pp.read_text(encoding='utf-8')[:2000]}\n"
                    except Exception:
                        repo_evidence += f"\n--- {rel} --- (unreadable)\n"
                else:
                    repo_evidence += f"\n--- {rel} --- (not found)\n"
            if task.acceptance:
                repo_evidence += "\nAcceptance:\n" + "\n".join(f"- {a}" for a in task.acceptance)
            user = f"Task: {task.description}\nBaseline:\n{baseline_text}\nRepo evidence:\n{repo_evidence[:3000]}\nTool evidence:\n{evidence_text[:2000]}\nPlan excerpt: {ctx.plan[:1500]}\nResult: {result.message[:500]}\nDiff is empty — decide if the repository already satisfies all acceptance criteria."
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

    def _review_local_no_diff(self, task, result) -> "AgentResult":
        from agent_system.agents.models import AgentResult as _AR

        hint_files = list(getattr(task, "files", None) or [])
        candidates = []
        for rel in hint_files:
            p = self.root / rel
            if p.is_file():
                candidates.append(rel)
        if candidates:
            print(f"    local mode: no git diff, accepting file existence: {candidates}")
            return _AR(status="SUCCESS", message=f"task {task.id} accepted (local, no diff)", artifacts=result.artifacts)
        print(f"    local mode: no diff and no expected files for {task.id}, still failing")
        return _AR(status="FAILED", message=f"task {task.id} produced no project changes", artifacts=result.artifacts)

    def generate_commit_message(self, diff: str, hint: str = "") -> str:
        p = Path(__file__).parent.parent / "prompts" / "parent" / "commit_message.md"
        system = p.read_text(encoding="utf-8") if p.exists() else _load_prompt("parent/commit_message") or "Generate a commit message."
        user = f"Diff:\n{diff[:6000]}\n\nHint:\n{hint[:1000]}" if hint else f"Diff:\n{diff[:6000]}"
        text = self._invoke(system, user)
        if text.startswith("mock result"):
            return "chore: preserve existing changes"
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
            user = f"Task: {task.description}\nAcceptance:\n{acc}\nValidation:\n{val}\nBaseline:\n{baseline_text}\nEvidence:\n{evidence_text[:3000]}\nPlan excerpt: {ctx.plan[:1500]}\nDiff:\n{diff[:3000]}\nResult: {result.message[:500]}"
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
        user = f"Task:\n{task}\n\nRepo: {ctx.repository}\n\nCLAUDE.md:\n{ctx.instructions[:2000]}\n\nMilestones:\n{milestones_text[:2000]}"
        invoked = self._invoke(system, user)
        if invoked and not invoked.startswith("mock result"):
            s = invoked.find("{")
            e = invoked.rfind("}")
            if s >= 0 and e > s:
                try:
                    import json as _pj

                    cand = _pj.loads(invoked[s:e+1])
                    if isinstance(cand, dict) and isinstance(cand.get("tasks"), list) and cand["tasks"]:
                        return _pj.dumps(cand, ensure_ascii=False)
                except Exception:
                    pass
            print("  [plan: invalid structured response, using structured fallback]")
        def _first_meaningful_line(t: str) -> str:
            for line in t.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                return s[:300]
            return "Implement feature"
        first_meaningful = _first_meaningful_line(task)
        import json as _jf

        # Try to derive files from task text
        import re as _re2

        _files_hint = _re2.findall(r"[\w./-]+\.py", task)
        fallback = {
            "objective": first_meaningful,
            "analysis": f"Task: {task.strip()[:500]} Repo: {ctx.repository or 'unknown'}",
            "tasks": [
                {
                    "id": "task001",
                    "role": "code",
                    "type": "implementation",
                    "description": first_meaningful,
                    "acceptance": ["Repository satisfies the task requirements", "Existing tests pass"],
                    "validation": ["Run existing test suite"],
                    "files": _files_hint[:3],
                }
            ],
            "risks": [],
        }
        return _jf.dumps(fallback, ensure_ascii=False)

    def _execute(self, plan: str, ctx: ProjectContext):
        raise RuntimeError("_execute is removed — no executable tasks must fail, not mock-execute")

    def _invoke(self, system: str, user: str) -> str:
        try:
            import anthropic

            api_key = resolve_api_key()
            if not api_key:
                return self._fallback(user)

            base_url = resolve_base_url()
            if base_url:
                client = anthropic.Anthropic(api_key=api_key, base_url=base_url, timeout=20)
            else:
                client = anthropic.Anthropic(api_key=api_key, timeout=20)
            kwargs = dict(
                model=self.model or "claude-sonnet-4-20250514",
                max_tokens=2048,
                messages=[{"role": "user", "content": user[:6000]}],
            )
            if system:
                kwargs["system"] = system[:4000]
            resp = client.messages.create(**kwargs)
            parts = []
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    parts.append(block.text)
            return "\n".join(parts)
        except Exception as e:
            raise RuntimeError(f"Parent API error: {e}") from e

    def _fallback(self, task: str, error: str = "") -> str:
        if error:
            print(f"  [fallback: {error[:80]}]")
        else:
            print("  [fallback: no ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN, using mock]")
        return f"mock result for task: {task[:80]}"
