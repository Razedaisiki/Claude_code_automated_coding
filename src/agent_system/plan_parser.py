import re
from pathlib import Path
from typing import List, Tuple

from agent_system.agents.models import AgentTask, TaskType


VERIFY_RE = re.compile(r"\b(verify|verification|validate|validation|syntax check|py_compile|smoke test|import test)\b", re.IGNORECASE)
OPTIONAL_RE = re.compile(r"\b(if appropriate|if consistent|if needed|optional|when appropriate|when needed)\b", re.IGNORECASE)


def classify(desc: str) -> Tuple[TaskType, bool]:
    if OPTIONAL_RE.search(desc):
        return "optional", False
    if VERIFY_RE.search(desc):
        return "verification", True
    if re.search(r"\b(verify|check|inspect|confirm|ensure proper|validate)\b", desc, re.IGNORECASE):
        return "verification", True
    return "implementation", True


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
            ttype, required = classify(desc)
            tasks.append(AgentTask(id=f"task{idx:03d}", role=role, description=desc, type=ttype, required=required))
        elif stripped.startswith("- "):
            desc = stripped[2:].strip()
            if desc:
                idx += 1
                role = "test" if re.search(r"\btest\b", desc, re.IGNORECASE) else "code"
                ttype, required = classify(desc)
                tasks.append(AgentTask(id=f"task{idx:03d}", role=role, description=desc, type=ttype, required=required))
    if not tasks:
        for line in plan_text.splitlines():
            stripped = line.strip()
            m = re.match(r"^\d+[\.\)]\s*(.+)", stripped)
            if m:
                desc = m.group(1).strip()
                idx += 1
                role = "test" if re.search(r"\btest\b", desc, re.IGNORECASE) else "code"
                ttype, required = classify(desc)
                tasks.append(AgentTask(id=f"task{idx:03d}", role=role, description=desc, type=ttype, required=required))
    return tasks


def parse_plan_file(path: Path) -> List[AgentTask]:
    return parse_plan(path.read_text(encoding="utf-8"))
