import json
import os
from pathlib import Path

from agent_system.context import ProjectContext
from agent_system.planning.config import PlannerConfig
from agent_system.planning.planner import StructuredPlanner


class FakeReasoning:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, *, system, user, max_tokens, timeout):
        self.calls.append((system, user, max_tokens, timeout))
        if not self.responses:
            raise RuntimeError("No more fake responses")
        return self.responses.pop(0)


def _ctx():
    return ProjectContext(task="Build a production super-resolution system", instructions="", milestones=[], plan="", repository="ref: refs/heads/main", plan_data=None)


def test_staged_planner_produces_many_tasks(tmp_path):
    # initial skeleton 8 -> refine 14 -> refine 18 -> converged
    skeleton = json.dumps({"objective": "SR system", "tasks": [{"title": f"Task {i}", "scope": f"Scope {i}"} for i in range(1, 9)]})
    refine1 = json.dumps({"changed": True, "tasks": [{"title": f"Task {i}", "scope": f"Scope {i}"} for i in range(1, 15)]})
    refine2 = json.dumps({"changed": True, "tasks": [{"title": f"Task {i}", "scope": f"Scope {i}"} for i in range(1, 19)]})
    refine3 = json.dumps({"changed": False, "tasks": [{"title": f"Task {i}", "scope": f"Scope {i}"} for i in range(1, 19)]})

    # Enrich batches (batch size 5): 18 tasks => 4 batches
    def enrich_batch(start, end):
        tasks = []
        for i in range(start, end + 1):
            tasks.append({
                "id": f"task{i:03d}",
                "role": "code",
                "type": "implementation",
                "description": f"Implement task {i} focused responsibility",
                "acceptance": [f"acceptance {i}"],
                "validation": [f"validation {i}"],
                "files": [f"src/task{i:03d}.py"],
            })
        return json.dumps({"tasks": tasks})

    enrich1 = enrich_batch(1, 5)
    enrich2 = enrich_batch(6, 10)
    enrich3 = enrich_batch(11, 15)
    enrich4 = enrich_batch(16, 18)

    fake = FakeReasoning([skeleton, refine1, refine2, refine3, enrich1, enrich2, enrich3, enrich4])
    config = PlannerConfig(max_tasks=32, max_tokens=8192, split_rounds=3, enrich_batch_size=5, repair_attempts=2, fallback_mode="error")
    planner = StructuredPlanner(root=tmp_path, reasoning=fake, config=config)
    result = planner.create_plan("Build SR", _ctx())
    data = json.loads(result)
    assert len(data["tasks"]) == 18
    assert data["tasks"][0]["id"] == "task001"
    assert data["tasks"][17]["id"] == "task018"
    assert data["planner"]["task_count"] == 18
    assert data["planner"]["split_rounds"] == 2
    for t in data["tasks"]:
        assert t["acceptance"]
        assert t["validation"]


def test_planner_uses_local_ids_deterministically(tmp_path):
    # Skeleton 2 tasks -> 1 batch of 2
    skel = json.dumps({"objective": "obj", "tasks": [{"title": "A", "scope": "s"}, {"title": "B", "scope": "s"}]})
    refine_done = json.dumps({"changed": False, "tasks": [{"title": "A", "scope": "s"}, {"title": "B", "scope": "s"}]})
    enrich = json.dumps({"tasks": [
        {"id": "task001", "role": "code", "type": "implementation", "description": "A", "acceptance": ["a"], "validation": ["v"], "files": []},
        {"id": "task002", "role": "code", "type": "implementation", "description": "B", "acceptance": ["a"], "validation": ["v"], "files": []},
    ]})
    fake = FakeReasoning([skel, refine_done, enrich])
    config = PlannerConfig(max_tasks=32, split_rounds=3, enrich_batch_size=5)
    planner = StructuredPlanner(root=tmp_path, reasoning=fake, config=config)
    result = json.loads(planner.create_plan("task", _ctx()))
    assert result["tasks"][0]["id"] == "task001"
    assert result["tasks"][1]["id"] == "task002"
