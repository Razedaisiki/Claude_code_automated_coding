import json

from agent_system.context import ProjectContext
from agent_system.planning.config import PlannerConfig
from agent_system.planning.planner import StructuredPlanner


class FakeReasoningRepair:
    def __init__(self):
        self.calls = 0

    def complete(self, *, system, user, max_tokens, timeout):
        self.calls += 1
        low = system.lower()
        is_repair = "repair" in low
        is_enrich = "task enrichment" in low
        is_skeleton = "task skeleton" in low and not is_enrich
        if is_enrich and not is_repair:
            if "task006" in user:
                tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(6, 11)]
                return json.dumps({"tasks": tasks})
            if "task011" in user:
                tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(11, 13)]
                return json.dumps({"tasks": tasks})
            tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(1, 6)]
            return json.dumps({"tasks": tasks})
        if is_repair and "Skeleton JSON" in user:
            tasks = [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 13)]
            return json.dumps({"objective": "repaired", "tasks": tasks})
        if is_skeleton and not is_repair and self.calls == 1:
            return "not json at all {{{"
        if is_skeleton and is_repair:
            tasks = [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 13)]
            return json.dumps({"objective": "repaired", "tasks": tasks})
        if is_skeleton and not is_repair:
            tasks = [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 13)]
            return json.dumps({"objective": "repaired", "tasks": tasks})
        if is_repair:
            if "task006" in user:
                tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(6, 11)]
                return json.dumps({"tasks": tasks})
            if "task011" in user:
                tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(11, 13)]
                return json.dumps({"tasks": tasks})
            if "title" in user.lower() or "Skeleton" in user:
                tasks = [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 13)]
                return json.dumps({"objective": "repaired", "tasks": tasks})
            tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(1, 6)]
            return json.dumps({"tasks": tasks})
        tasks = [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 13)]
        return json.dumps({"objective": "repaired", "tasks": tasks})


def _fake_reasoning_for_enrich_repair():
    calls = {"n": 0}
    def complete(*, system, user, max_tokens, timeout):
        calls["n"] += 1
        low = system.lower()
        is_repair = "repair" in low
        if is_repair:
            if "task006" in user:
                tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(6, 11)]
                return json.dumps({"tasks": tasks})
            if "task011" in user:
                tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(11, 13)]
                return json.dumps({"tasks": tasks})
            tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(1, 6)]
            return json.dumps({"tasks": tasks})
        if calls["n"] == 1:
            return json.dumps({"objective": "obj", "tasks": [{"title": f"T{i}", "scope": "s"} for i in range(1, 13)]})
        if calls["n"] == 2:
            return json.dumps({"changed": False, "tasks": [{"title": f"T{i}", "scope": "s"} for i in range(1, 13)]})
        if calls["n"] == 3:
            return "malformed {{{{"
        if calls["n"] == 4:
            tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(1, 6)]
            return json.dumps({"tasks": tasks})
        if calls["n"] == 5:
            tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(6, 11)]
            return json.dumps({"tasks": tasks})
        if calls["n"] == 6:
            tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(11, 13)]
            return json.dumps({"tasks": tasks})
        raise RuntimeError(f"unexpected call {calls['n']}")
    class Fake:
        def complete(self, *, system, user, max_tokens, timeout):
            return complete(system=system, user=user, max_tokens=max_tokens, timeout=timeout)
    return Fake()


def _ctx():
    from agent_system.context import ProjectContext
    return ProjectContext(task="Big task", instructions="", milestones=[], plan="", repository="", plan_data=None)


def test_repair_path_keeps_task_count(tmp_path):
    # Use a simpler repair that only affects skeleton (no enrich ID mismatch)
    class SimpleRepairFake:
        def __init__(self):
            self.calls = 0
        def complete(self, *, system, user, max_tokens, timeout):
            self.calls += 1
            low = system.lower()
            if self.calls == 1 and "task skeleton" in low:
                return "not json {{{"
            if "repair" in low and "Skeleton" in user:
                tasks = [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 13)]
                return json.dumps({"objective": "repaired", "tasks": tasks})
            if "task skeleton" in low:
                tasks = [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 13)]
                return json.dumps({"objective": "repaired", "tasks": tasks})
            # Enrich batches
            if "task006" in user:
                tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(6, 11)]
                return json.dumps({"tasks": tasks})
            if "task011" in user:
                tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(11, 13)]
                return json.dumps({"tasks": tasks})
            tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(1, 6)]
            return json.dumps({"tasks": tasks})
    fake = SimpleRepairFake()
    config = PlannerConfig(max_tasks=32, repair_attempts=2, enrich_batch_size=5, split_rounds=0)
    planner = StructuredPlanner(root=tmp_path, reasoning=fake, config=config)
    result = json.loads(planner.create_plan("Big task", _ctx()))
    assert len(result["tasks"]) == 12
    assert result["tasks"][0]["id"] == "task001"
    assert result["tasks"][-1]["id"] == "task012"


def test_repair_does_not_collapse(tmp_path):
    fake = _fake_reasoning_for_enrich_repair()
    config = PlannerConfig(max_tasks=32, repair_attempts=2, enrich_batch_size=5, split_rounds=1)
    planner = StructuredPlanner(root=tmp_path, reasoning=fake, config=config)
    result = json.loads(planner.create_plan("Big task", _ctx()))
    assert len(result["tasks"]) == 12
    assert result["planner"]["fallback_used"] is False


def test_repair_collapse_rejected(tmp_path):
    import pytest
    from agent_system.planning.planner import PlanningError
    class CollapsingFake:
        def __init__(self):
            self.calls = 0
        def complete(self, *, system, user, max_tokens, timeout):
            self.calls += 1
            low = system.lower()
            is_repair = "repair" in low
            if not is_repair and self.calls == 1:
                return json.dumps({"objective": "o", "tasks": [{"title": f"T{i}", "scope": "s"} for i in range(1, 13)]})
            if not is_repair and self.calls == 2:
                return json.dumps({"changed": False, "tasks": [{"title": f"T{i}", "scope": "s"} for i in range(1, 13)]})
            if not is_repair and self.calls == 3:
                tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(1,6)]
                return json.dumps({"tasks": tasks})
            if not is_repair and self.calls == 4:
                tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(6,11)]
                return json.dumps({"tasks": tasks})
            if not is_repair and self.calls == 5:
                tasks = [{"id": f"task{i:03d}", "role": "code", "type": "implementation", "description": f"D{i}", "acceptance": [], "validation": ["v"], "files": []} for i in range(11,13)]
                return json.dumps({"tasks": tasks})
            # Validation repairs collapse to 1 -> planner must reject
            return json.dumps({"objective": "o", "tasks": [{"id": "task001", "role": "code", "type": "implementation", "description": "collapsed", "acceptance": ["a"], "validation": ["v"], "files": []}], "risks": []})
    fake = CollapsingFake()
    config = PlannerConfig(max_tasks=32, repair_attempts=2, enrich_batch_size=5, split_rounds=1, fallback_mode="error")
    planner = StructuredPlanner(root=tmp_path, reasoning=fake, config=config)
    with pytest.raises(PlanningError) as exc:
        planner.create_plan("Big task", _ctx())
    assert "tasks[10]" in str(exc.value) or "tasks[11]" in str(exc.value)
    fake2 = CollapsingFake()
    fake2.calls = 0
    config2 = PlannerConfig(max_tasks=32, repair_attempts=1, enrich_batch_size=5, split_rounds=1, fallback_mode="single_task")
    planner2 = StructuredPlanner(root=tmp_path, reasoning=fake2, config=config2)
    result2 = json.loads(planner2.create_plan("Big task", _ctx()))
    assert len(result2["tasks"]) == 1
    assert result2["planner"]["fallback_used"] is True
