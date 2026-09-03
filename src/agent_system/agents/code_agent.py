from __future__ import annotations

from pathlib import Path

from agent_system.agents.models import AgentResult, AgentTask
from agent_system.agents.subagent import SubAgent
from agent_system.contracts.coding import CodingBackend


class CodeAgent(SubAgent):
    def __init__(self, backend: CodingBackend, root: Path | None = None):
        self.root = Path(root).resolve() if root else Path.cwd().resolve()
        self.backend: CodingBackend = backend

    @classmethod
    def with_default_backend(cls, root: Path | None = None, model: str | None = None) -> "CodeAgent":
        from agent_system.backends.claude_code.backend import ClaudeCodeBackend
        root = Path(root).resolve() if root else Path.cwd().resolve()
        return cls(backend=ClaudeCodeBackend(root=root, model=model), root=root)

    def execute(self, task: AgentTask, baseline=None) -> AgentResult:
        print(f"  [CodeAgent] {task.id}: {task.description}")
        if task.files:
            print(f"  files: {', '.join(task.files)}")
        from agent_system.context import load_context

        ctx = load_context(self.root)
        result = self.backend.execute(task, ctx, baseline=baseline)
        if result.message and not result.message.startswith("mock"):
            print(f"  result: {result.message[:120]}")
        print(f"  changed: {result.artifacts if result.artifacts else 'none'}")
        print("  CodeAgent done")
        return result
