from pathlib import Path

from agent_system.supervisor.session import SessionManager
from agent_system.supervisor.state import StateManager


def _default_parent(root: Path):
    try:
        from agent_system.agents.claude_parent import ClaudeParentAgent

        return ClaudeParentAgent(root=root)
    except Exception:
        from agent_system.agents.mock_parent import MockParent

        return MockParent()


class Supervisor:
    def __init__(self, root: Path = None, parent=None):
        self.root = root or Path.cwd()
        self.state = StateManager(self.root)
        self.sessions = SessionManager(self.root)
        self.parent = parent or _default_parent(self.root)

    def start(self):
        print("Supervisor started")
        from agent_system.runtime.git import Git

        git = Git(self.root)
        status = git.status()
        if status.strip():
            print("Workspace has existing changes.")
            print("Preparing pre-workflow snapshot...")
            diff = git.diff()
            try:
                from agent_system.agents.claude_parent import ClaudeParentAgent

                parent = ClaudeParentAgent(root=self.root)
                msg = parent.generate_commit_message(diff)
            except Exception:
                msg = "chore: preserve existing changes"
            out = git.commit(msg)
            print(f"Pre-workflow snapshot committed: {msg}")
            if out.strip():
                print(f"  {out.strip()[:120]}")

        session = self.sessions.create()
        self.state.update(status="RUNNING", session_id=session["id"])
        print(f"State {self.state.load()['status']} session {session['id']}")

        task = session["task"]
        try:
            result = self.parent.run(task)
            if hasattr(result, "status") and result.status == "FAILED":
                self.state.update(status="FAILED")
                print(f"State {self.state.load()['status']}")
                print(f"Parent failed: {result.message}")
                return result
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
        return result if "result" in locals() else None

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
            result = self.parent.run(task)
            if hasattr(result, "status") and result.status == "FAILED":
                self.state.update(status="FAILED")
                print(f"State {self.state.load()['status']}")
                return result
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
