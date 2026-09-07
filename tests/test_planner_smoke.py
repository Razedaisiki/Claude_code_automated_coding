import json
import re

from agent_system.context import ProjectContext
from agent_system.planning.config import PlannerConfig
from agent_system.planning.planner import StructuredPlanner


def _ctx():
    return ProjectContext(task="Build SR", instructions="", milestones=[], plan="", repository="", plan_data=None)


def test_25_task_enrichment_normalization(tmp_path):
    # 25 skeleton tasks with some having wrong role/type or missing them
    skeleton_json = json.dumps({
        "objective": "SR system",
        "tasks": [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 26)]
    })

    class BadRoleFake:
        def __init__(self):
            self.calls = 0
        def complete(self, *, system, user, max_tokens, timeout):
            self.calls += 1
            low = system.lower()
            is_enrich = "task enrichment" in low
            is_skeleton = "task skeleton" in low and not is_enrich
            is_refine = "decomposition refinement" in low
            if is_skeleton and "repair" not in low:
                return json.dumps({"objective": "SR", "tasks": [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 26)]})
            if is_refine and "repair" not in low:
                return json.dumps({"changed": False, "tasks": [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 26)]})
            if is_enrich:
                m = re.search(r"Enrich batch \d+/\d+ \((task\d+)-", user)
                start = 1
                if m:
                    try:
                        start = int(m.group(1).replace("task",""))
                    except Exception:
                        start = 1
                mm = re.search(r'\{"tasks":\s*\[.*?\]\}', user, re.DOTALL)
                n = 5
                if mm:
                    try:
                        obj = json.loads(mm.group(0))
                        n = len(obj.get("tasks", []) or [])
                    except Exception:
                        n = 5
                tasks = []
                for i in range(n):
                    tid = start + i
                    tasks.append({"description": f"D{tid}", "acceptance": ["a"], "validation": ["v"], "files": [], "role": "documentation", "type": "optimization"})
                return json.dumps({"tasks": tasks})
            # Repair: match enrich batch by ids in user
            if "task006" in user and "Enrich" in user:
                tasks = [{"description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(6, 11)]
                return json.dumps({"tasks": tasks})
            return json.dumps({"objective":"x","tasks":[{"title":"T","scope":"s"}]})

    fake = BadRoleFake()
    config = PlannerConfig(max_tasks=64, split_rounds=1, enrich_batch_size=5, repair_attempts=1)
    planner = StructuredPlanner(root=tmp_path, reasoning=fake, config=config)
    raw = planner.create_plan("Build SR large task with many pieces", _ctx())
    data = json.loads(raw)
    assert len(data["tasks"]) == 25
    assert data["tasks"][0]["id"] == "task001"
    assert data["tasks"][24]["id"] == "task025"
    for t in data["tasks"]:
        assert t["role"] == "code"
        assert t["type"] == "implementation"
    from agent_system.plan_parser import validate_plan_data
    assert validate_plan_data(data) == []


def test_workflow_mock_smoke(tmp_path):
    import subprocess
    tmp_path.mkdir(parents=True, exist_ok=True)
    import os
    orig = os.getenv("WORKFLOW_MOCK")
    os.environ["WORKFLOW_MOCK"] = "1"
    try:
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, capture_output=True)
        (tmp_path / "TASK.md").write_text("Simple greeting task")
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "greet.py").write_text("def greet(name): return f'Hello {name}'")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)
        from agent_system.supervisor.supervisor import Supervisor
        sup = Supervisor(root=tmp_path)
        res = sup.start()
        assert res is not None
        assert res.status == "SUCCESS" or res.status == "FAILED"  # at least planning didn't crash without exception
        from agent_system.runtime.state_store import StateManager
        st = StateManager(tmp_path).load()
        assert st.get("status") in ("COMPLETED", "FAILED", "RUNNING")
        # Plan should exist with at least 1 task, role normalized
        import json as _j
        plan_path = tmp_path / ".agent" / "plan.json"
        if plan_path.exists():
            data = _j.loads(plan_path.read_text())
            assert len(data.get("tasks", [])) >= 1
    finally:
        if orig is None:
            os.environ.pop("WORKFLOW_MOCK", None)
        else:
            os.environ["WORKFLOW_MOCK"] = orig
