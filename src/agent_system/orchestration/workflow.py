from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_system.agents.models import AgentResult
from agent_system.context import load_context
from agent_system.plan_parser import load_plan, parse_plan, parse_plan_json, render_plan_md
from agent_system.runtime.git import Git


class WorkflowOrchestrator:
    def __init__(self, root: Path, *, tech_lead, task_runtime):
        self.root = Path(root or Path.cwd()).resolve()
        self.tech_lead = tech_lead
        self.task_runtime = task_runtime
        self.git = Git(self.root)

    def generate_commit_message(self, diff: str) -> str:
        return self.tech_lead.generate_commit_message(diff)

    def get_context(self):
        return load_context(self.root)

    def run(self, task: str, session_id: str = None) -> AgentResult:
        print("Starting workflow")
        # Resolve frozen session context
        state_mgr = None
        delivery = {}
        is_resume = False
        sid = session_id
        frozen_task = None
        frozen_plan_data = None
        frozen_plan_text = None
        try:
            from agent_system.runtime.state_store import StateManager as _SM2
            state_mgr = _SM2(self.root)
            st = state_mgr.load()
            is_resume = st.get("execution_mode") == "RESUME"
            delivery = st.get("delivery") or {}
            sid = sid or st.get("session_id")
        except Exception:
            is_resume = False
        if sid:
            try:
                from agent_system.supervisor.session import SessionManager as _Sess
                sess = _Sess(self.root).get(sid)
                if sess:
                    frozen_task = sess.get("task", "")
                pd, pt = _Sess(self.root).get_plan(sid)
                if pd is not None or (pt and pt.strip()):
                    frozen_plan_data, frozen_plan_text = pd, pt
            except Exception:
                pass
        # Load context with frozen overrides if resume
        if is_resume and sid and frozen_task is not None:
            ctx = load_context(self.root, task_override=frozen_task, plan_override=frozen_plan_text or "", plan_data_override=frozen_plan_data)
        else:
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

        # Plan handling
        plan_data = None
        plan_text = ""
        tasks = []
        if is_resume and sid and frozen_plan_data is not None:
            # Use frozen session plan as authority
            plan_data = frozen_plan_data
            plan_text = frozen_plan_text or ""
            if plan_data is not None:
                tasks = parse_plan_json(plan_data)
            if not tasks and plan_text.strip():
                tasks = parse_plan(plan_text)
            if tasks:
                print("  plan reused from frozen session")
            else:
                # Frozen plan invalid but exists - fail
                print("  Planning FAILED: frozen session plan invalid")
                return AgentResult(status="FAILED", message="frozen session plan invalid", artifacts=[])
        elif is_resume and sid and frozen_plan_text and frozen_plan_text.strip():
            plan_text = frozen_plan_text
            tasks = parse_plan(plan_text)
            if not tasks and frozen_plan_data:
                tasks = parse_plan_json(frozen_plan_data)
            if tasks:
                print("  plan reused from frozen session (md)")
        elif is_resume and sid:
            # Resume but no frozen plan yet -> need to (re)plan using frozen task
            task_for_planning = frozen_task if frozen_task is not None else (ctx.task or task)
            raw = self.tech_lead.plan(task_for_planning, ctx)
            try:
                s = raw.find("{")
                e = raw.rfind("}")
                if s >= 0 and e > s:
                    cand = json.loads(raw[s:e+1])
                    if isinstance(cand.get("tasks"), list):
                        plan_data = cand
            except Exception:
                plan_data = None
            if plan_data is not None:
                tasks = parse_plan_json(plan_data)
                if not tasks:
                    print("  Planning FAILED: structured plan contains no valid executable tasks")
                    return AgentResult(status="FAILED", message="structured plan contains no valid executable tasks", artifacts=[])
                # validate role/type combos
                err = self._validate_tasks(tasks)
                if err:
                    return AgentResult(status="FAILED", message=err, artifacts=[])
                # freeze plan
                from agent_system.runtime.atomic_io import atomic_write_json, atomic_write_text
                from agent_system.supervisor.session import SessionManager as _Sess2
                atomic_write_json(plan_json_file, plan_data)
                pt2 = render_plan_md(plan_data)
                atomic_write_text(plan_file, pt2)
                _Sess2(self.root).freeze_plan(sid, plan_data, pt2)
                # update plan_sha
                try:
                    plan_sha = hashlib.sha256(json.dumps(plan_data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
                    state_mgr.update(plan_sha256=plan_sha)
                except Exception:
                    pass
                plan_text = pt2
            else:
                plan_text = raw
                tasks = parse_plan(plan_text)
                err = self._validate_tasks(tasks)
                if err:
                    return AgentResult(status="FAILED", message=err, artifacts=[])
                from agent_system.runtime.atomic_io import atomic_write_text as _awt
                from agent_system.supervisor.session import SessionManager as _Sess3
                _awt(plan_file, plan_text)
                _Sess3(self.root).freeze_plan(sid, None, plan_text)
            if not tasks:
                print("  Planning FAILED: no executable tasks produced")
                return AgentResult(status="FAILED", message="planning produced no executable tasks", artifacts=[])
            print(f"  parsed {len(tasks)} tasks from plan")
            for t in tasks:
                print(f"    {t.id} [{t.role}/{t.type}] {t.description[:80]}")
        else:
            # Non-resume: fresh planning using current task
            task_for_planning = ctx.task or task
            raw = self.tech_lead.plan(task_for_planning, ctx)
            try:
                s = raw.find("{")
                e = raw.rfind("}")
                if s >= 0 and e > s:
                    cand = json.loads(raw[s:e+1])
                    if isinstance(cand.get("tasks"), list):
                        plan_data = cand
            except Exception:
                plan_data = None
            if plan_data is not None:
                from agent_system.runtime.atomic_io import atomic_write_json, atomic_write_text
                plan_json_file.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(plan_json_file, plan_data)
                plan_text = render_plan_md(plan_data)
                plan_file.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(plan_file, plan_text)
                print("  plan.json created")
                print("  plan.md rendered")
                tasks = parse_plan_json(plan_data)
                if not tasks:
                    print("  Planning FAILED: structured plan contains no valid executable tasks")
                    return AgentResult(status="FAILED", message="structured plan contains no valid executable tasks", artifacts=[])
            else:
                plan_text = raw
                plan_file.parent.mkdir(parents=True, exist_ok=True)
                from agent_system.runtime.atomic_io import atomic_write_text as _awt2
                _awt2(plan_file, plan_text)
                print("  plan.md created")
                tasks = parse_plan(plan_text)
            err = self._validate_tasks(tasks)
            if err:
                return AgentResult(status="FAILED", message=err, artifacts=[])
            # freeze to session if we have sid
            if sid:
                try:
                    from agent_system.supervisor.session import SessionManager as _SessF
                    _SessF(self.root).freeze_plan(sid, plan_data, plan_text)
                    if plan_data is not None:
                        plan_sha = hashlib.sha256(json.dumps(plan_data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
                    else:
                        plan_sha = hashlib.sha256(plan_text.encode("utf-8")).hexdigest()
                    if state_mgr:
                        state_mgr.update(plan_sha256=plan_sha)
                except Exception:
                    pass
        if not tasks:
            print("  Planning FAILED: no executable tasks produced")
            return AgentResult(status="FAILED", message="planning produced no executable tasks", artifacts=[])
        # Validate role/type combos
        err = self._validate_tasks(tasks)
        if err:
            return AgentResult(status="FAILED", message=err, artifacts=[])
        print(f"  parsed {len(tasks)} tasks from plan")
        for t in tasks:
            print(f"    {t.id} [{t.role}/{t.type}] {t.description[:80]}")

        from agent_system.runtime.state_store import StateManager as _SM

        _st = _SM(self.root).load()
        is_resume2 = _st.get("execution_mode") == "RESUME"
        delivery2 = _st.get("delivery") or {}
        start_idx = 0
        if is_resume2:
            from agent_system.runtime.checkpoint import TaskPhase
            completed = delivery2.get("completed_task_index")
            current = delivery2.get("current_task_index")
            if delivery2.get("phase") == TaskPhase.TASK_COMPLETED.value and isinstance(completed, int):
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
            resume_current = is_resume2 and task_index == start_idx and delivery2.get("phase") not in (None, "", "TASK_COMPLETED")
            print(f"  Dispatch: {original.id} -> {original.role}/{original.type} (index {task_index})")
            res = self.task_runtime.run_task(original, task_index, resume_current=resume_current)
            if res.status == "FAILED":
                return res

        print("Workflow finished")
        return AgentResult(status="SUCCESS", message="plan executed", artifacts=[str(plan_file)], next_action="DONE")

    def _validate_tasks(self, tasks) -> str:
        for t in tasks:
            if t.type == "verification":
                if t.role != "test":
                    return f"verification task {t.id} must have role=test"
                if not t.validation:
                    return f"verification task {t.id} must have at least one validation command"
            if t.type == "optional" and t.role not in ("code", "test"):
                return f"optional task {t.id} invalid role"
            if t.role == "test" and t.type not in ("verification", "optional"):
                return f"test role task {t.id} must be verification or optional"
        return ""
