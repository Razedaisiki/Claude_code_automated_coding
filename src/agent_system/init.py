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

    state = {"status": "INITIALIZED", "session_id": None, "prompt_version": "v0.3"}
    (agent_dir / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    cfg = agent_dir / "config.yaml"
    if cfg.exists():
        text = cfg.read_text(encoding="utf-8")
        if "delivery:" not in text:
            with cfg.open("a", encoding="utf-8") as f:
                if not text.endswith("\n"):
                    f.write("\n")
                f.write("delivery:\n  mode: local\n")
                f.write("prompt_version: v0.3\n" if "prompt_version" not in text else "")
        elif "prompt_version" not in text:
            with cfg.open("a", encoding="utf-8") as f:
                f.write("prompt_version: v0.3\n")
        else:
            if "v0.2" in text:
                cfg.write_text(text.replace("v0.2", "v0.3"), encoding="utf-8")
    else:
        cfg.write_text(
            "version: 0.1.0\nmodel: mock\nprompt_version: v0.3\ndelivery:\n  mode: local\n",
            encoding="utf-8",
        )

    src_prompts = Path(__file__).parent / "prompts"
    if src_prompts.exists():
        for item in src_prompts.rglob("*"):
            if item.is_file():
                rel = item.relative_to(src_prompts)
                dest = prompts_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(item, dest)

    from agent_system.runtime.git import Git

    Git(root).ensure_runtime_isolation()

    task_file = root / "TASK.md"
    if not task_file.exists():
        task_file.write_text("# Task\n\nDescribe your task here.\n", encoding="utf-8")

    return agent_dir
