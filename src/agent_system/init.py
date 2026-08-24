import json
from pathlib import Path


def init_workspace(root: Path = None):
    root = root or Path.cwd()
    agent_dir = root / ".agent"
    prompts_dir = agent_dir / "prompts"
    milestones_dir = agent_dir / "milestones"

    prompts_dir.mkdir(parents=True, exist_ok=True)
    milestones_dir.mkdir(parents=True, exist_ok=True)

    state = {"status": "INITIALIZED", "session_id": None}
    (agent_dir / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    (agent_dir / "config.yaml").write_text(
        "version: 0.1.0\nmodel: mock\n", encoding="utf-8"
    )

    (prompts_dir / "parent.md").write_text("# Parent Agent Prompt\n\nYou are the parent agent.\n", encoding="utf-8")
    (prompts_dir / "code.md").write_text("# Code Agent Prompt\n\nYou are the code agent.\n", encoding="utf-8")
    (prompts_dir / "test.md").write_text("# Test Agent Prompt\n\nYou are the test agent.\n", encoding="utf-8")

    task_file = root / "TASK.md"
    if not task_file.exists():
        task_file.write_text("# Task\n\nDescribe your task here.\n", encoding="utf-8")

    return agent_dir
