from abc import ABC, abstractmethod

from agent_system.agents.models import AgentResult, AgentTask


class SubAgent(ABC):
    @abstractmethod
    def execute(self, task: AgentTask) -> AgentResult:
        pass


class MockCodeAgent(SubAgent):
    def execute(self, task: AgentTask) -> AgentResult:
        print(f"  [CodeAgent] {task.id}: {task.description}")
        print("  CodeAgent done")
        return AgentResult(status="SUCCESS", message=f"mock code: {task.description}", artifacts=[], next_action="")


class MockTestAgent(SubAgent):
    def execute(self, task: AgentTask) -> AgentResult:
        print(f"  [TestAgent] {task.id}: {task.description}")
        print("  TestAgent done")
        return AgentResult(status="SUCCESS", message=f"mock test: {task.description}", artifacts=[], next_action="")
