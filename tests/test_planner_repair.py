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
        # Batch repair: return correctly without id/role/type
        if is_repair:
            if "task006" in user:
                tasks = [{"description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(6, 11)]
                return json.dumps({"tasks": tasks})
            if "task011" in user:
                tasks = [{"description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(11, 13)]
                return json.dumps({"tasks": tasks})
            tasks = [{"description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(1, 6)]
            return json.dumps({"tasks": tasks})
        if calls["n"] == 1:
            return json.dumps({"objective": "obj", "tasks": [{"title": f"T{i}", "scope": "s"} for i in range(1, 13)]})
        if calls["n"] == 2:
            return json.dumps({"changed": False, "tasks": [{"title": f"T{i}", "scope": "s"} for i in range(1, 13)]})
        if calls["n"] == 3:
            return "malformed {{{{"
        if calls["n"] == 4:
            tasks = [{"description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(1, 6)]
            return json.dumps({"tasks": tasks})
        if calls["n"] == 5:
            tasks = [{"description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(6, 11)]
            return json.dumps({"tasks": tasks})
        if calls["n"] == 6:
            tasks = [{"description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(11, 13)]
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
    # Tests that skeleton repair preserves task count and deterministic IDs still work
    # Now that role/type are deterministic, enrich no longer validates IDs via LLM — test simpler invariant
    class SimpleRepairFake:
        def __init__(self):
            self.calls = 0
    def complete(self, *, system, user, max_tokens, timeout):
        self.calls += 1
        low = system.lower()
        is_enrich = "task enrichment" in low
        is_skeleton = "task skeleton" in low and not is_enrich
        if is_skeleton and not "repair" in low and self.calls == 1:
            return "not json {{{"
        if "repair" in low and "Skeleton" in user:
            tasks = [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 13)]
            return json.dumps({"objective": "repaired", "tasks": tasks})
        if is_skeleton:
            tasks = [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 13)]
            return json.dumps({"objective": "repaired", "tasks": tasks})
        if is_enrich:
            if "task006" in user:
                tasks = [{"description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(6, 11)]
                return json.dumps({"tasks": tasks})
            if "task011" in user:
                tasks = [{"description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(11, 13)]
                return json.dumps({"tasks": tasks})
            tasks = [{"description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(1, 6)]
            return json.dumps({"tasks": tasks})
        tasks = [{"title": f"T{i}", "scope": f"S{i}"} for i in range(1, 13)]
        return json.dumps({"objective": "repaired", "tasks": tasks})


def test_repair_does_not_collapse(tmp_path):
    config = PlannerConfig(max_tasks=32, repair_attempts=2, enrich_batch_size=5, split_rounds=0)
    planner = StructuredPlanner(root=tmp_path, reasoning=fake, config=config)
    result = json.loads(planner.create_plan("Big task", _ctx()))
    assert len(result["tasks"]) == 12
    assert result["tasks"][0]["id"] == "task001"
    assert result["tasks"][0]["role"] == "code"
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
    class InvalidBatchFake:
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
                tasks = [{"description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(1,6)]
                return json.dumps({"tasks": tasks})
            if not is_repair and self.calls == 4:
                tasks = [{"description": f"D{i}", "acceptance": ["a"], "validation": ["v"], "files": []} for i in range(6,11)]
                return json.dumps({"tasks": tasks})
            if not is_repair and self.calls == 5:
                return json.dumps({"not_tasks": []})
            if is_repair:
                return json.dumps({"not_tasks": []})
            return json.dumps({"tasks": [{"description": "x", "acceptance": ["a"], "validation": ["v"], "files": []} for _ in range(2)]})
    fake = InvalidBatchFake()
    config = PlannerConfig(max_tasks=32, repair_attempts=1, enrich_batch_size=5, split_rounds=1, fallback_mode="error")
    planner = StructuredPlanner(root=tmp_path, reasoning=fake, config=config)
    with pytest.raises(PlanningError) as exc:
        planner.create_plan("Big task", _ctx())
    assert "enrichment batch 3" in str(exc.value).lower()
    fake2 = InvalidBatchFake()
    config2 = PlannerConfig(max_tasks=32, repair_attempts=1, enrich_batch_size=5, split_rounds=1, fallback_mode="single_task")
    planner2 = StructuredPlanner(root=tmp_path, reasoning=fake2, config=config2)
    with pytest.raises(PlanningError):
        planner2.create_plan("Big task", _ctx())
