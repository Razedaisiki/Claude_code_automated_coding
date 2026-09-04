from pathlib import Path

from agent_system.supervisor.session import SessionManager
from agent_system.supervisor.state import StateManager


def _default_workflow(root: Path):
    from agent_system.composition import build_default_workflow

    return build_default_workflow(root)


class Supervisor:
    def __init__(self, root: Path = None, workflow=None, parent=None):
        self.root = root or Path.cwd()
        self.state = StateManager(self.root)
        self.sessions = SessionManager(self.root)
        # handle both workflow and parent for compat: if parent and not workflow: workflow = parent
        if parent is not None and workflow is None:
            workflow = parent
        self.workflow = workflow or _default_workflow(self.root)
        self.parent = self.workflow  # alias for compat

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
            from agent_system.runtime.checkpoint import Checkpoint as _CP
            _CP(self.root).validate()
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
                msg = self.workflow.generate_commit_message(diff)
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
            result = self.workflow.run(task)
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
            from agent_system.runtime.checkpoint import Checkpoint as _CP2
            _CP2(self.root).validate()
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
            result = self.workflow.run(task)
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
