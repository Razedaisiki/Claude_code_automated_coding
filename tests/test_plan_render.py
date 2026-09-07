from agent_system.plan_parser import render_plan_md, parse_plan, render_plan_context


def test_render_plan_md_large_preserves_fields():
    tasks = []
    for i in range(1, 21):
        tasks.append({
            "id": f"task{i:03d}",
            "role": "code",
            "type": "implementation",
            "description": f"Implement feature {i} focused responsibility",
            "acceptance": [f"acceptance {i}"],
            "validation": [f"validation {i}"],
            "files": [f"src/task{i:03d}.py"],
        })
    data = {"objective": "Build SR system", "analysis": "Decomposed", "tasks": tasks, "risks": ["risk"], "planner": {"version": 2, "strategy": "staged", "task_count": 20}}
    md = render_plan_md(data)
    parsed = parse_plan(md)
    assert len(parsed) == 20
    assert parsed[0].id == "task001"
    assert parsed[9].id == "task010"
    assert parsed[19].id == "task020"
    assert parsed[0].role == "code"
    assert parsed[0].type == "implementation"
    assert "Implement feature 1" in parsed[0].description
    assert parsed[5].acceptance == ["acceptance 6"]
    assert parsed[5].validation == ["validation 6"]
    assert parsed[5].files == ["src/task006.py"]


def test_render_plan_context_current_and_neighbors():
    tasks = [{"id": f"task{i:03d}", "description": f"Feature {i}"} for i in range(1, 11)]
    data = {"objective": "Obj", "tasks": tasks}
    ctx = render_plan_context(data, current_task_id="task005", max_chars=4000)
    assert "task005" in ctx and "current" in ctx.lower()
    assert "Previous:" in ctx
    assert "Next:" in ctx
    # truncation
    ctx_small = render_plan_context(data, current_task_id="task005", max_chars=50)
    assert "truncated" in ctx_small.lower()
