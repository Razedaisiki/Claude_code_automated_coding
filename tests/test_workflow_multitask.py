import json
import subprocess
from pathlib import Path

from agent_system.context import ProjectContext
from agent_system.plan_parser import parse_plan_json
from agent_system.planning.config import PlannerConfig
from agent_system.planning.planner import StructuredPlanner


class FakeReasoningMultiTask:
    def __init__(self):
        self.calls = 0

    def complete(self, *, system, user, max_tokens, timeout):
        self.calls += 1
        if self.calls == 1:
            tasks = [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 13)]
            return json.dumps({"objective": "multi", "tasks": tasks})
        if self.calls == 2:
            tasks = [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 13)]
            return json.dumps({"changed": False, "tasks": tasks})
        # Enrich batches: batch size 5 -> 3 batches (5,5,2)
        batch_map = {3: (1, 5), 4: (6, 10), 5: (11, 12)}
        if self.calls in batch_map:
            s, e = batch_map[self.calls]
            tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"Feature {i}", "acceptance": [f"a{i}"], "validation": [f"v{i}"], "files": []} for i in range(s, e + 1)]
            return json.dumps({"tasks": tasks})
        raise RuntimeError(f"unexpected call {self.calls}")


def _ctx():
    return ProjectContext(task="Multi task project", instructions="", milestones=[], plan="", repository="", plan_data=None)


def test_workflow_writes_multitask_plan(tmp_path):
    # Directly test StructuredPlanner -> plan.json freeze via WorkflowOrchestrator composition
    reasoning = FakeReasoningMultiTask()
    config = PlannerConfig(max_tasks=32, split_rounds=1, enrich_batch_size=5)
    planner = StructuredPlanner(root=tmp_path, reasoning=reasoning, config=config)
    raw = planner.create_plan("Multi task project", _ctx())
    data = json.loads(raw)
    assert len(data["tasks"]) == 12

    # Simulate WorkflowOrchestrator freeze
    tmp_path.mkdir(parents=True, exist_ok=True)
    from agent_system.runtime.atomic_io import atomic_write_json, atomic_write_text
    from agent_system.plan_parser import render_plan_md
    from agent_system.supervisor.session import SessionManager
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
    (tmp_path / "TASK.md").write_text("Multi task project")
    # Init session
    sess_mgr = SessionManager(tmp_path)
    sess = sess_mgr.create(task_sha256="abc", start_head_sha="")
    # Write via workflow path
    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    atomic_write_json(tmp_path / ".agent" / "plan.json", data)
    atomic_write_text(tmp_path / ".agent" / "plan.md", render_plan_md(data))
    sess_mgr.freeze_plan(sess["id"], data, render_plan_md(data))
    # Check frozen
    pd, pt = sess_mgr.get_plan(sess["id"])
    assert pd is not None
    assert len(pd["tasks"]) == 12
    assert (tmp_path / ".agent" / "plan.md").read_text().count("task012") >= 1
    # Check file contains task001 and task012
    md_text = (tmp_path / ".agent" / "plan.md").read_text()
    assert "task001" in md_text
    assert "task012" in md_text


def test_workflow_must_not_regenerate_on_resume(tmp_path):
    # Ensure frozen plan not overwritten
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
    (tmp_path / "a.txt").write_text("init")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
    from agent_system.supervisor.session import SessionManager
    from agent_system.runtime.state_store import StateManager
    from agent_system.plan_parser import parse_plan_json
    # Create session with frozen plan
    reasoning = FakeReasoningMultiTask()
    config = PlannerConfig(max_tasks=32, split_rounds=1, enrich_batch_size=5)
    planner = StructuredPlanner(root=tmp_path / "sub" if (tmp_path / "sub").exists() else tmp_path, reasoning=reasoning, config=config)
    # Use main tmp_path for simplicity with already created session
    raw = planner.create_plan("Multi", _ctx())
    data = json.loads(raw)
    sess_mgr = SessionManager(tmp_path)
    sess = sess_mgr.create(task_sha256="abc", start_head_sha="abc")
    sess_mgr.freeze_plan(sess["id"], data, "")
    # Simulate resume would load frozen plan, not regenerate
    pd2, _ = sess_mgr.get_plan(sess["id"])
    assert len(pd2["tasks"]) == 12
    # Even if TASK.md changes, frozen plan remains
    (tmp_path / "TASK.md").write_text("Different task entirely that would produce 2 tasks")
    pd3, _ = sess_mgr.get_plan(sess["id"])
    assert len(pd3["tasks"]) == 12
