import json
import shutil
from pathlib import Path


def init_workspace(root: Path = None):
    root = root or Path.cwd()
    agent_dir = root / ".agent"
    prompts_dir = agent_dir / "prompts"
    milestones_dir = agent_dir / "milestones"

    prompts_dir.mkdir(parents=True, exist_ok=True)
    milestones_dir.mkdir(parents=True, exist_ok=True)

    state = {"status": "INITIALIZED", "session_id": None, "prompt_version": "v0.2"}
    (agent_dir / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    (agent_dir / "config.yaml").write_text(
        "version: 0.1.0\nmodel: mock\nprompt_version: v0.2\n", encoding="utf-8"
    )

    src_prompts = Path(__file__).parent / "prompts"
    if src_prompts.exists():
        for item in src_prompts.rglob("*"):
            if item.is_file():
                rel = item.relative_to(src_prompts)
                dest = prompts_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(item, dest)

    task_file = root / "TASK.md"
    if not task_file.exists():
        task_file.write_text("# Task\n\nDescribe your task here.\n", encoding="utf-8")

    return agent_dir
