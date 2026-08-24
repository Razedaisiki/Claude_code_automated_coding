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
        try:
            self.parent.run(task)
        except KeyboardInterrupt:
            self.state.update(status="FAILED")
            print(f"State {self.state.load()['status']}")
            raise
        except Exception:
            self.state.update(status="FAILED")
            print(f"State {self.state.load()['status']}")
            raise

        self.state.update(status="COMPLETED")
        print(f"State {self.state.load()['status']}")

    def resume(self):
        state = self.state.load()
        sid = state.get("session_id")
        if not sid:
            print("No session to resume")
            return
        session = self.sessions.get(sid)
        if not session:
            print(f"Session {sid} not found")
            return
        print(f"Found session: {sid}")
        print("Resume workflow")
        self.state.update(status="RUNNING")
        print(f"State {self.state.load()['status']} session {sid}")
        task = session["task"]
        try:
            self.parent.run(task)
        except KeyboardInterrupt:
            self.state.update(status="FAILED")
            print(f"State {self.state.load()['status']}")
            raise
        except Exception:
            self.state.update(status="FAILED")
            print(f"State {self.state.load()['status']}")
            raise
        self.state.update(status="COMPLETED")
        print(f"State {self.state.load()['status']}")
        print("DONE")

    def stop(self):
        self.state.update(status="COMPLETED")
