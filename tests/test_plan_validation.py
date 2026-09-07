from agent_system.plan_parser import validate_plan_data, is_valid_plan_data, parse_plan_json


def _valid_task(n, extra=None):
    t = {
        "id": f"task{n:03d}",
        "description": f"Implement feature {n}",
        "acceptance": ["acceptance 1"],
        "validation": ["validation 1"],
        "files": ["src/foo.py"],
        "role": "code",
        "type": "implementation",
    }
    if extra:
        t.update(extra)
    return t


def test_validator_accepts_many_tasks():
    tasks = [_valid_task(i) for i in range(1, 21)]
    data = {"tasks": tasks}
    assert validate_plan_data(data) == []
    assert is_valid_plan_data(data) is True
    assert len(parse_plan_json(data)) == 20


def test_exceeds_safety_cap_returns_error():
    tasks = [_valid_task(i) for i in range(1, 34)]
    data = {"tasks": tasks}
    errors = validate_plan_data(data, max_tasks=32)
    assert any("safety limit is 32" in e and "33 tasks" in e for e in errors)


def test_validator_returns_precise_path():
    tasks = [_valid_task(i) for i in range(1, 8)]
    tasks[6]["validation"] = None
    errors = validate_plan_data({"tasks": tasks})
    assert any("tasks[6].validation" in e for e in errors)
    # also test role check
    tasks2 = [_valid_task(1, extra={"role": "wrong"})]
    errors2 = validate_plan_data({"tasks": tasks2})
    assert any("tasks[0].role" in e for e in errors2)


def test_duplicate_id_error():
    tasks = [_valid_task(1), _valid_task(1)]
    tasks[1]["id"] = "task001"
    errors = validate_plan_data({"tasks": tasks})
    assert any("duplicate task id" in e.lower() for e in errors)


def test_verification_requires_validation():
    tasks = [_valid_task(1, extra={"role": "test", "type": "verification", "validation": []})]
    errors = validate_plan_data({"tasks": tasks})
    assert any("tasks[0].validation" in e and "verification" in e for e in errors)
    tasks[0]["validation"] = ["run tests"]
    assert validate_plan_data({"tasks": tasks}) == []


def test_implementation_role_mismatch():
    tasks = [_valid_task(1, extra={"role": "test", "type": "implementation"})]
    errors = validate_plan_data({"tasks": tasks})
    assert any('tasks[0].role must be "code"' in e for e in errors)
