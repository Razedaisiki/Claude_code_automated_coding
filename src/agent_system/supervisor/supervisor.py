from pathlib import Path

from agent_system.supervisor.state import StateManager


class _MockParent:
    def run(self, task: str = ""):
        print("Parent started")
        if task:
            print(f"Task: {task[:80]}")
        print("Parent finished")


class Supervisor:
    def __init__(self, root: Path = None):
        self.root = root or Path.cwd()
        self.state = StateManager(self.root)
        self.parent = _MockParent()

    def start(self):
        print("Supervisor started")
        self.state.update(status="RUNNING")
        print(f"State {self.state.load()['status']}")

        task = ""
        task_file = self.root / "TASK.md"
        if task_file.exists():
            task = task_file.read_text(encoding="utf-8")

        self.parent.run(task)

        self.state.update(status="COMPLETED")
        print(f"State {self.state.load()['status']}")

    def stop(self):
        self.state.update(status="COMPLETED")
