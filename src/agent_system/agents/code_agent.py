from pathlib import Path

from agent_system.agents.models import AgentResult, AgentTask
from agent_system.agents.subagent import SubAgent
from agent_system.runtime.coding import ClaudeCodeRuntime


class CodeAgent(SubAgent):
    def __init__(self, root: Path = None, model: str = None):
        self.root = root or Path.cwd()
        self.model = model
        self.runtime = ClaudeCodeRuntime(root=self.root, model=model)

    def execute(self, task: AgentTask) -> AgentResult:
        print(f"  [CodeAgent] {task.id}: {task.description}")
        if task.files:
            print(f"  files: {', '.join(task.files)}")
        from agent_system.context import load_context

        ctx = load_context(self.root)
        result = self.runtime.execute(task, ctx)
        if result.message and not result.message.startswith("mock"):
            print(f"  result: {result.message[:120]}")
        print(f"  changed: {result.artifacts if result.artifacts else 'none'}")
        print("  CodeAgent done")
        return result
