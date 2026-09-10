import hashlib
from pathlib import Path

from agent_system.supervisor.session import SessionManager
from agent_system.supervisor.state import StateManager


def _default_workflow(root: Path):
    from agent_system.composition import build_default_workflow

    return build_default_workflow(root)


def has_unfinished_session(state: dict) -> bool:
    from agent_system.runtime.state_store import UNFINISHED_PHASES
    if not state.get("session_id"):
        return False
    delivery = state.get("delivery") or {}
    phase = delivery.get("phase")
    if phase in UNFINISHED_PHASES:
        return True
    if state.get("status") == "RUNNING" and phase:
        return True
    return False


class Supervisor:
    def __init__(self, root: Path = None, workflow=None, parent=None):
        self.root = Path(root or Path.cwd()).resolve()
        self.state = StateManager(self.root)
        self.sessions = SessionManager(self.root)
        if parent is not None and workflow is None:
            workflow = parent
        self.workflow = workflow or _default_workflow(self.root)
        self.parent = self.workflow

    def _finalize_parent_result(self, result):
        if hasattr(result, "status") and result.status == "FAILED":
            self.state.update(event="workflow_failed", status="FAILED")
            print(f"State {self.state.load()['status']}")
            return result
        try:
            from agent_system.runtime.checkpoint import Checkpoint, TaskPhase

            Checkpoint(self.root).validate()
            delivery = self.state.load().get("delivery") or {}
            if delivery and delivery.get("phase") != TaskPhase.TASK_COMPLETED.value:
                self.state.update(event="workflow_failed", status="FAILED")
                print(f"State {self.state.load()['status']}")
                from agent_system.agents.models import AgentResult

                return AgentResult(status="FAILED", message=f"Parent returned SUCCESS with unfinished phase {delivery.get('phase')}", artifacts=[])
            from agent_system.runtime.git import Git
            snap = Git(self.root).capture_tree_snapshot()
            if snap.has_changes:
                self.state.update(event="workflow_failed", status="FAILED")
                print(f"State {self.state.load()['status']}")
                from agent_system.agents.models import AgentResult
                return AgentResult(status="FAILED", message="Finalization guard failed: workflow cannot complete with pending project changes", artifacts=snap.changed_files)
        except Exception as e:
            self.state.update(event="workflow_failed", status="FAILED")
            print(f"State {self.state.load()['status']}")
            from agent_system.agents.models import AgentResult

            return AgentResult(status="FAILED", message=f"finalization guard failed: {e}", artifacts=[])
        # Write session result for milestone provenance
        try:
            state = self.state.load()
            sid = state.get("session_id")
            if sid:
                session = self.sessions.get(sid)
                from agent_system.runtime.git import Git as _G
                end_sha = _G(self.root).head_sha() or ""
                start_sha = (session or {}).get("start_head_sha") or state.get("workflow_start_sha") or ""
                self.sessions.write_result(sid, {"session_id": sid, "completed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "start_head_sha": start_sha, "end_head_sha": end_sha, "task_history": state.get("task_history") or []})
        except Exception:
            pass
        self.state.update(event="workflow_completed", status="COMPLETED")
        print(f"State {self.state.load()['status']}")
        return result

    def start(self):
        print("Supervisor started")
        from agent_system.runtime.workspace_lock import WorkspaceLock, WorkspaceLockError
        lock = WorkspaceLock(self.root, operation="run")
        try:
            lock.acquire()
        except WorkspaceLockError as e:
            print(str(e))
            from agent_system.agents.models import AgentResult
            return AgentResult(status="FAILED", message=str(e), artifacts=[])
        try:
            return self._start_locked()
        finally:
            lock.release()

    def _start_locked(self):
        from agent_system.runtime.git import Git

        git = Git(self.root)
        if not git.is_supported_layout():
            print("Unsupported Git layout: linked worktrees are not currently supported.")
            from agent_system.agents.models import AgentResult
            return AgentResult(status="FAILED", message="Unsupported Git layout: linked worktrees are not currently supported.", artifacts=[])
        if not git.is_workspace_repo():
            print("Cannot start workflow: workspace must be an initialized Git repository.")
            print(f"Expected Git root: {self.root.resolve()}")
            print("Initialize Git in this project before running workflow (e.g. git init).")
            self.state.update(event="workflow_failed", status="FAILED")
            print(f"State {self.state.load()['status']}")
            return
        try:
            from agent_system.runtime.checkpoint import Checkpoint as _CP
            _CP(self.root).validate()
        except RuntimeError as e:
            print(f"State validation failed: {e}")
            self.state.update(event="workflow_failed", status="FAILED")
            print(f"State {self.state.load()['status']}")
            return
        state = self.state.load()
        if has_unfinished_session(state):
            sid = state.get("session_id")
            print(f"Cannot start a new workflow: unfinished session {sid} exists.")
            print("Use `workflow resume`.")
            from agent_system.agents.models import AgentResult
            return AgentResult(status="FAILED", message=f"Cannot start a new workflow: unfinished session {sid} exists. Use `workflow resume`.", artifacts=[])
        git.ensure_runtime_isolation()
        # Detect legitimate pre-workflow changes via tree snapshot
        snap = git.capture_tree_snapshot()
        if snap.has_changes:
            print("Workspace has existing changes.")
            print("Preparing pre-workflow snapshot...")
            # Create exact commit via commit-tree
            tree_sha = snap.tree_sha
            parent = snap.base_commit_sha
            head_ref = git.head_ref()
            # Fallback pre-workflow message
            diff = snap.diff[:500] if snap.diff else ""
            try:
                msg = self.workflow.generate_commit_message(diff)
            except Exception:
                msg = "chore: preserve existing changes"
            if not head_ref:
                # Need branch; try to infer via init logic: if no commits yet, create initial branch commit
                head_ref = "refs/heads/main"
            pending = git.create_commit_object(tree_sha, parent, msg)
            if not pending:
                err = getattr(git, "_last_commit_error", "") or "commit-tree produced no commit object"
                print(f"Pre-workflow snapshot FAILED: commit-tree failed — {err}")
                if "Git identity" in err or "user.name" in err:
                    print("Hint: SSH sessions often lack global git identity. Run:")
                    print("  git config --global user.name \"Your Name\"")
                    print("  git config --global user.email \"you@example.com\"")
                self.state.update(event="workflow_failed", status="FAILED")
                print(f"State {self.state.load()['status']}")
                return
            ok = git.update_ref(head_ref, pending, parent) if parent else git.update_ref(head_ref, pending)
            if not ok:
                err2 = getattr(git, "_last_update_ref_error", "") or "unknown"
                print(f"Pre-workflow snapshot FAILED: update-ref failed — {err2}")
                self.state.update(event="workflow_failed", status="FAILED")
                print(f"State {self.state.load()['status']}")
                return
            git.sync_index_to_head()
            print(f"Pre-workflow snapshot committed: {msg} [{pending[:7]}]")

        task_text = ""
        task_file = self.root / "TASK.md"
        if task_file.exists():
            task_text = task_file.read_text(encoding="utf-8")
        task_sha = hashlib.sha256(task_text.encode("utf-8")).hexdigest()
        start_head = git.head_sha() or ""
        session = self.sessions.create(task_sha256=task_sha, start_head_sha=start_head)
        # Update session start_head after snapshot
        self.sessions.update_start_head(session["id"], start_head)
        self.state.start_new_execution(session["id"], task_sha256=task_sha, workflow_start_sha=start_head)
        print(f"State {self.state.load()['status']} session {session['id']}")

        task = session["task"]
        try:
            result = self.workflow.run(task, session_id=session["id"])
            return self._finalize_parent_result(result)
        except KeyboardInterrupt:
            print("Workflow interrupted; checkpoint preserved for resume.")
            raise
        except BaseException:
            # Don't overwrite completed state
            try:
                cur = self.state.load()
                if cur.get("status") != "COMPLETED":
                    self.state.update(event="workflow_failed", status="FAILED")
                    print(f"State {self.state.load()['status']}")
            except Exception:
                pass
            raise

    def resume(self):
        from agent_system.runtime.workspace_lock import WorkspaceLock, WorkspaceLockError
        lock = WorkspaceLock(self.root, operation="resume")
        try:
            lock.acquire()
        except WorkspaceLockError as e:
            print(str(e))
            from agent_system.agents.models import AgentResult
            return AgentResult(status="FAILED", message=str(e), artifacts=[])
        try:
            return self._resume_locked()
        finally:
            lock.release()

    def _resume_locked(self):
        from agent_system.runtime.git import Git
        git = Git(self.root)
        if not git.is_supported_layout():
            print("Unsupported Git layout: linked worktrees are not currently supported.")
            from agent_system.agents.models import AgentResult
            return AgentResult(status="FAILED", message="Unsupported Git layout: linked worktrees are not currently supported.", artifacts=[])
        try:
            from agent_system.runtime.checkpoint import Checkpoint as _CP2
            _CP2(self.root).validate()
        except RuntimeError as e:
            print(f"Cannot resume: {e}")
            self.state.update(event="workflow_failed", status="FAILED")
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
        # Verify task_sha
        task_file = self.root / "TASK.md"
        if task_file.exists():
            cur_task = task_file.read_text(encoding="utf-8")
            cur_sha = hashlib.sha256(cur_task.encode("utf-8")).hexdigest()
            sess_sha = session.get("task_sha256") or state.get("task_sha256") or ""
            if sess_sha and cur_sha != sess_sha:
                print(f"Warning: TASK.md has changed since session {sid} started; using frozen session task.")
        print(f"Found session: {sid}")
        print("Resume workflow")
        self.state.update(event="workflow_resumed", status="RUNNING", execution_mode="RESUME")
        print(f"State {self.state.load()['status']} session {sid}")
        task = session["task"]
        try:
            result = self.workflow.run(task, session_id=sid)
            return self._finalize_parent_result(result)
        except KeyboardInterrupt:
            print("Workflow interrupted; checkpoint preserved for resume.")
            raise
        except BaseException:
            try:
                cur = self.state.load()
                if cur.get("status") != "COMPLETED":
                    self.state.update(event="workflow_failed", status="FAILED")
                    print(f"State {self.state.load()['status']}")
            except Exception:
                pass
            raise

    def stop(self):
        self.state.update(event="workflow_completed", status="COMPLETED")
