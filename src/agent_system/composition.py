from __future__ import annotations

from pathlib import Path
import os
from agent_system.providers.anthropic.provider import AnthropicReasoningProvider
from agent_system.providers.anthropic.config import resolve_anthropic_model
from agent_system.backends.claude_code.backend import ClaudeCodeBackend
from agent_system.orchestration.tech_lead import TechLead
from agent_system.orchestration.workflow import WorkflowOrchestrator
from agent_system.runtime.task_runtime import TaskRuntime

def build_default_workflow(root: Path | None = None, model: str | None = None):
    root = Path(root or Path.cwd())
    if os.getenv("XXX_MOCK") == "1":
        from agent_system.agents.mock_parent import MockParent
        mp = MockParent()
        # Return an adapter that exposes same interface as WorkflowOrchestrator
        class MockWorkflow:
            def __init__(self, parent): self._parent = parent; self.root = root
            @property
            def tech_lead(self): return self._parent  # for supervisor snapshot compat
            def run(self, task): return self._parent.run(task)
            def generate_commit_message(self, diff):
                if hasattr(self._parent, "generate_commit_message"):
                    return self._parent.generate_commit_message(diff)
                return "chore: preserve existing changes"
        return MockWorkflow(mp)
    reasoning = AnthropicReasoningProvider(model=model)
    tech_lead = TechLead(root=root, reasoning=reasoning)
    coding_backend = ClaudeCodeBackend(root=root, model=model or reasoning.model)
    task_runtime = TaskRuntime(root=root, coding_backend=coding_backend, tech_lead=tech_lead)
    return WorkflowOrchestrator(root=root, tech_lead=tech_lead, task_runtime=task_runtime)
