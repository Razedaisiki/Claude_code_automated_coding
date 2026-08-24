from pathlib import Path

from agent_system.agents.mock_parent import MockParent
from agent_system.supervisor.session import SessionManager
from agent_system.supervisor.state import StateManager


class Supervisor:
    def __init__(self, root: Path = None, parent=None):
        self.root = root or Path.cwd()
        self.state = StateManager(self.root)
        self.sessions = SessionManager(self.root)
        self.parent = parent or MockParent()

    def start(self):
        print("Supervisor started")
        session = self.sessions.create()
        self.state.update(status="RUNNING", session_id=session["id"])
        print(f"State {self.state.load()['status']} session {session['id']}")

        task = session["task"]
        self.parent.run(task)

        self.state.update(status="COMPLETED")
        print(f"State {self.state.load()['status']}")

    def stop(self):
        self.state.update(status="COMPLETED")
