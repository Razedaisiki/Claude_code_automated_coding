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
        # satisfaction review: empty diff path — user contains the satisfaction prompt suffix
        if "already satisfies all acceptance criteria" in low_user:
            return '{"decision":"ALREADY_SATISFIED","reason":"mock satisfied","evidence":[]}'
        if self.CI_REVIEW_MARKER in low_system:
            return '{"decision":"APPROVED","reason":"mock ci approved"}'
        if self.COMMIT_MARKER in low_system:
            return "chore: mock commit"
        if self.PLAN_MARKER in low_system:
            return '{"tasks":[{"id":"task001","description":"mock task","acceptance":["mock"],"validation":[],"files":[],"role":"code","type":"implementation"}],"objective":"mock","analysis":"mock","risks":[]}'
        # default: normal review
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
