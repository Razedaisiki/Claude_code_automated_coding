from __future__ import annotations

from pathlib import Path

from agent_system.agents.models import AgentResult
from agent_system.context import load_context
from agent_system.plan_parser import load_plan, parse_plan, parse_plan_json, render_plan_md
from agent_system.runtime.git import Git


class WorkflowOrchestrator:
    def __init__(self, root: Path, *, tech_lead, task_runtime):
        self.root = Path(root or Path.cwd())
        self.tech_lead = tech_lead
        self.task_runtime = task_runtime
        self.git = Git(self.root)

    def generate_commit_message(self, diff: str) -> str:
        return self.tech_lead.generate_commit_message(diff)

    def get_context(self):
        return load_context(self.root)

    def run(self, task: str) -> AgentResult:
        print("Starting workflow")
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
            from agent_system.runtime.state_store import StateManager as _SM2

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
            raw = self.tech_lead.plan(ctx.task or task, ctx)
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

        from agent_system.runtime.state_store import StateManager as _SM
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
            res = self.task_runtime.run_task(original, task_index, resume_current=resume_current)
            if res.status == "FAILED":
                return res

        print("Workflow finished")
        return AgentResult(status="SUCCESS", message="plan executed", artifacts=[str(plan_file)], next_action="DONE")
