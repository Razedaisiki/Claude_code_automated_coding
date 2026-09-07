from __future__ import annotations

from pathlib import Path
import os
from agent_system.providers.anthropic.provider import AnthropicReasoningProvider
from agent_system.backends.claude_code.backend import ClaudeCodeBackend
from agent_system.orchestration.tech_lead import TechLead
from agent_system.orchestration.workflow import WorkflowOrchestrator
from agent_system.runtime.task_runtime import TaskRuntime

class _MockReasoningProvider:
    PLAN_MARKER = "executable engineering plan"
    COMMIT_MARKER = "commit message"
    CI_REVIEW_MARKER = "analyzing ci results"

    def complete(self, *, system: str, user: str, max_tokens: int, timeout: int) -> str:
        low_system = (system or "").lower()
        low_user = (user or "").lower()
        # Staged planner markers
        if "task skeleton" in low_system:
            return '{"objective":"mock skeleton","tasks":[{"title":"Mock task","scope":"Mock scope"}]}'
        if "decomposition refinement" in low_system:
            return '{"changed":false,"tasks":[{"title":"Mock task","scope":"Mock scope"}]}'
        if "task enrichment" in low_system:
            # Extract expected count from user if possible, else 1
            import re, json
            m = re.search(r"Enrich batch \d+/\d+ \((task\d+)-", user)
            start = 1
            if m:
                try:
                    start = int(m.group(1).replace("task",""))
                except Exception:
                    start = 1
            # Count tasks in the batch input
            import re as _re
            # Rough: count occurrences of task\d+ in user
            batch_tasks = []
            # Try to parse the skeleton batch JSON in user
            import json as _js
            try:
                import re as __re
                mm = __re.search(r'\{"tasks":\s*\[.*?\]\}', user, re.DOTALL)
                if mm:
                    obj = json.loads(mm.group(0))
                    n = len(obj.get("tasks", []) or [])
                    for i in range(n):
                        tid = f"task{start+i:03d}"
                        batch_tasks.append({"description": f"Mock task {tid}", "acceptance": ["mock"], "validation": ["mock"], "files": []})
                else:
                    batch_tasks = [{"description": "Mock task", "acceptance": ["mock"], "validation": ["mock"], "files": []}]
            except Exception:
                batch_tasks = [{"description": "Mock task", "acceptance": ["mock"], "validation": ["mock"], "files": []}]
            if not batch_tasks:
                batch_tasks = [{"description": "Mock task", "acceptance": ["mock"], "validation": ["mock"], "files": []}]
            return json.dumps({"tasks": batch_tasks})
        if "json repair" in low_system:
            # Generic repair: return a valid skeleton/enrich based on what's asked
            if "skeleton" in low_user or "title" in low_user.lower():
                return '{"objective":"mock","tasks":[{"title":"Mock task","scope":"Mock scope"}]}'
            if "task00" in user:
                import re, json
                ids = re.findall(r"task\d+", user)
                if ids:
                    return json.dumps({"tasks": [{"description": f"Mock {tid}", "acceptance": ["mock"], "validation": ["mock"], "files": []} for tid in ids]})
            return '{"tasks":[{"description":"Mock task","acceptance":["mock"],"validation":["mock"],"files":[]}]}'
        if "already satisfies all acceptance criteria" in low_user:
            return '{"decision":"ALREADY_SATISFIED","reason":"mock satisfied","evidence":[]}'
        if self.CI_REVIEW_MARKER in low_system:
            return '{"decision":"APPROVED","reason":"mock ci approved"}'
        if self.COMMIT_MARKER in low_system:
            return "chore: mock commit"
        if self.PLAN_MARKER in low_system:
            return '{"tasks":[{"id":"task001","description":"mock task","acceptance":["mock"],"validation":[],"files":[],"role":"code","type":"implementation"}],"objective":"mock","analysis":"mock","risks":[]}'
        return '{"decision":"APPROVED","reason":"mock approved"}'


class _MockCodingBackend:
    def execute(self, task, context, baseline=None):
        from agent_system.agents.models import AgentResult
        return AgentResult(status="SUCCESS", message=f"mock code for {task.description}", artifacts=task.files, baseline=baseline)


def build_default_workflow(
    root: Path | None = None,
    model: str | None = None,
    *,
    reasoning_model: str | None = None,
    coding_model: str | None = None,
):
    root = Path(root or Path.cwd())
    if reasoning_model is None and model is not None:
        reasoning_model = model
    if os.getenv("WORKFLOW_MOCK") == "1":
        reasoning = _MockReasoningProvider()
        tech_lead = TechLead(root=root, reasoning=reasoning)
        coding_backend = _MockCodingBackend()
        task_runtime = TaskRuntime(root=root, coding_backend=coding_backend, tech_lead=tech_lead)
        return WorkflowOrchestrator(root=root, tech_lead=tech_lead, task_runtime=task_runtime)
    reasoning = AnthropicReasoningProvider(model=reasoning_model)
    tech_lead = TechLead(root=root, reasoning=reasoning)
    coding_backend = ClaudeCodeBackend(root=root, model=coding_model)
    task_runtime = TaskRuntime(root=root, coding_backend=coding_backend, tech_lead=tech_lead)
    return WorkflowOrchestrator(root=root, tech_lead=tech_lead, task_runtime=task_runtime)
