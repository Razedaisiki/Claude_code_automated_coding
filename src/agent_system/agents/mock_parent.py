from agent_system.agents.parent import ParentAgent


class MockParent(ParentAgent):
    def run(self, task: str) -> None:
        print("Parent Agent:")
        print("  reading task")
        if task:
            preview = task.strip().splitlines()[0][:60] if task.strip() else ""
            if preview:
                print(f"  task: {preview}")
        print("  planning")
        print("  completed")
