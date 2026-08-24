from agent_system.agents.models import AgentResult
from agent_system.agents.parent import ParentAgent


class MockParent(ParentAgent):
    def run(self, task: str) -> AgentResult:
        print("Parent Agent:")
        print("  reading task")
        if task:
            preview = task.strip().splitlines()[0][:60] if task.strip() else ""
            if preview:
                print(f"  task: {preview}")
        print("  planning")
        print("  completed")
        return AgentResult(status="SUCCESS", message="mock done", artifacts=[], next_action="EXECUTE")
