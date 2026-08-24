from abc import ABC, abstractmethod
from pathlib import Path

from agent_system.agents.models import AgentResult, AgentTask
from agent_system.context import ProjectContext


class CodingRuntime(ABC):
    @abstractmethod
    def execute(self, task: AgentTask, context: ProjectContext) -> AgentResult:
        pass


class ClaudeCodeRuntime(CodingRuntime):
    def __init__(self, root: Path = None, model: str = None):
        self.root = root or Path.cwd()
        self.model = model

    def execute(self, task: AgentTask, context: ProjectContext) -> AgentResult:
        from agent_system.agents.code_agent import CodeAgent

        agent = CodeAgent(root=self.root, model=self.model)
        return agent.execute(task)
