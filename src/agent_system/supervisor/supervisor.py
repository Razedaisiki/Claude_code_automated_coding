from pathlib import Path

from agent_system.supervisor.session import SessionManager
from agent_system.supervisor.state import StateManager


def _default_parent(root: Path):
    import os

    if os.getenv("XXX_MOCK") == "1":
        from agent_system.agents.mock_parent import MockParent

        return MockParent()
    from agent_system.config import resolve_api_key

    api_key = resolve_api_key()
    if not api_key:
        raise RuntimeError("API key not configured. Set ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN or use XXX_MOCK=1 for mock mode.")
    from agent_system.agents.claude_parent import ClaudeParentAgent

    return ClaudeParentAgent(root=root)


class Supervisor:
    def __init__(self, root: Path = None, parent=None):
        self.root = root or Path.cwd()
        self.state = StateManager(self.root)
        self.sessions = SessionManager(self.root)
        self.parent = parent or _default_parent(self.root)

    def _finalize_parent_result(self, result):
        if hasattr(result, "status") and result.status == "FAILED":
            self.state.update(status="FAILED")
            print(f"State {self.state.load()['status']}")
            return result
        try:
            from agent_system.runtime.checkpoint import Checkpoint, TaskPhase

            Checkpoint(self.root).validate()
            delivery = self.state.load().get("delivery") or {}
            if delivery and delivery.get("phase") != TaskPhase.TASK_COMPLETED.value:
                self.state.update(status="FAILED")
                print(f"State {self.state.load()['status']}")
                from agent_system.agents.models import AgentResult

                return AgentResult(status="FAILED", message=f"Parent returned SUCCESS with unfinished phase {delivery.get('phase')}", artifacts=[])
            from agent_system.runtime.git import Git
            changes = Git(self.root).project_changes_model()
            if changes.has_changes:
                self.state.update(status="FAILED")
                print(f"State {self.state.load()['status']}")
                from agent_system.agents.models import AgentResult
                return AgentResult(status="FAILED", message="Finalization guard failed: workflow cannot complete with pending project changes", artifacts=changes.changed_files)
        except Exception as e:
            self.state.update(status="FAILED")
            print(f"State {self.state.load()['status']}")
            from agent_system.agents.models import AgentResult

            return AgentResult(status="FAILED", message=f"finalization guard failed: {e}", artifacts=[])
        self.state.update(status="COMPLETED")
        print(f"State {self.state.load()['status']}")
        return result

    def start(self):
        print("Supervisor started")
        from agent_system.runtime.git import Git

        git = Git(self.root)
        if not git.is_workspace_repo():
            print("Cannot start workflow: workspace must be an initialized Git repository.")
            print(f"Expected Git root: {self.root.resolve()}")
            print("Initialize Git in this project before running xxx (e.g. git init).")
            self.state.update(status="FAILED")
            print(f"State {self.state.load()['status']}")
            return
        try:
            self.state.validate()
        except RuntimeError as e:
            print(f"State validation failed: {e}")
            self.state.update(status="FAILED")
            print(f"State {self.state.load()['status']}")
            return
        git.ensure_runtime_isolation()
        changes = git.project_changes_model()
        if changes.has_changes:
            print("Workspace has existing changes.")
            print("Preparing pre-workflow snapshot...")
            diff = changes.diff
            try:
                from agent_system.agents.claude_parent import ClaudeParentAgent

                parent = ClaudeParentAgent(root=self.root)
                msg = parent.generate_commit_message(diff)
            except Exception:
                msg = "chore: preserve existing changes"
            out = git.commit(msg)
            if out.get("status") != "SUCCESS":
                print(f"Pre-workflow snapshot FAILED: {out.get('message','')[:120]}")
                self.state.update(status="FAILED")
                print(f"State {self.state.load()['status']}")
                return
            print(f"Pre-workflow snapshot committed: {msg} [{out.get('sha','')[:7]}]")
            if out.get("message","").strip():
                print(f"  {out.get('message','')[:120]}")

        session = self.sessions.create()
        self.state.start_new_execution(session["id"])
        print(f"State {self.state.load()['status']} session {session['id']}")

        task = session["task"]
        try:
            result = self.parent.run(task)
            return self._finalize_parent_result(result)
        except KeyboardInterrupt:
            print("Workflow interrupted; checkpoint preserved for resume.")
            raise
        except Exception:
            self.state.update(status="FAILED")
            print(f"State {self.state.load()['status']}")
            raise

    def resume(self):
        try:
            self.state.validate()
        except RuntimeError as e:
            print(f"Cannot resume: {e}")
            self.state.update(status="FAILED")
            print(f"State {self.state.load()['status']}")
            return
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
        self.state.update(status="RUNNING", execution_mode="RESUME")
        print(f"State {self.state.load()['status']} session {sid}")
        task = session["task"]
        try:
            result = self.parent.run(task)
            return self._finalize_parent_result(result)
        except KeyboardInterrupt:
            print("Workflow interrupted; checkpoint preserved for resume.")
            raise
        except Exception:
            self.state.update(status="FAILED")
            print(f"State {self.state.load()['status']}")
            raise

    def stop(self):
        self.state.update(status="COMPLETED")
