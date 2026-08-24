import re
from pathlib import Path
from typing import List

from agent_system.agents.models import AgentTask


def parse_plan(plan_text: str) -> List[AgentTask]:
    tasks: List[AgentTask] = []
    in_tasks = False
    idx = 0
    for line in plan_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# Tasks"):
            in_tasks = True
            continue
        if in_tasks and stripped.startswith("#"):
            break
        if not in_tasks:
            continue
        m = re.match(r"^\d+[\.\)]\s*(.+)", stripped)
        if m:
            desc = m.group(1).strip()
            idx += 1
            role = "test" if re.search(r"\btest\b", desc, re.IGNORECASE) else "code"
            tasks.append(AgentTask(id=f"task{idx:03d}", role=role, description=desc))
        elif stripped.startswith("- "):
            desc = stripped[2:].strip()
            if desc:
                idx += 1
                role = "test" if re.search(r"\btest\b", desc, re.IGNORECASE) else "code"
                tasks.append(AgentTask(id=f"task{idx:03d}", role=role, description=desc))
    if not tasks:
        for line in plan_text.splitlines():
            stripped = line.strip()
            m = re.match(r"^\d+[\.\)]\s*(.+)", stripped)
            if m:
                desc = m.group(1).strip()
                idx += 1
                role = "test" if re.search(r"\btest\b", desc, re.IGNORECASE) else "code"
                tasks.append(AgentTask(id=f"task{idx:03d}", role=role, description=desc))
    return tasks


def parse_plan_file(path: Path) -> List[AgentTask]:
    return parse_plan(path.read_text(encoding="utf-8"))
