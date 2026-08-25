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
    idx = 0

    id_re = re.compile(r"^ID:\s*(.+)", re.IGNORECASE)
    type_re = re.compile(r"^Type:\s*(.+)", re.IGNORECASE)
    desc_re = re.compile(r"^Description:\s*(.+)", re.IGNORECASE)

    lines = plan_text.splitlines()
    i = 0
    in_tasks = False
    pending_id = None
    pending_type = None

    while i < len(lines):
        stripped = lines[i].strip()

        if re.match(r"^#{1,2}\s*Tasks\s*$", stripped, re.IGNORECASE):
            in_tasks = True
            i += 1
            continue
        if in_tasks and re.match(r"^#{1,2}\s+(Objective|Analysis|Risks|Execution)", stripped):
            break
        if not in_tasks:
            i += 1
            continue

        if re.match(r"^(Acceptance Criteria|Acceptance|Validation)\s*:", stripped, re.IGNORECASE):
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if id_re.match(nxt) or desc_re.match(nxt) or type_re.match(nxt) or re.match(r"^#{1,2}\s+", nxt):
                    break
                i += 1
            continue

        m_id = id_re.match(stripped)
        if m_id:
            pending_id = m_id.group(1).strip().lower()
            i += 1
            continue
        m_type = type_re.match(stripped)
        if m_type:
            pending_type = m_type.group(1).strip().lower()
            i += 1
            continue
        m_desc = desc_re.match(stripped)
        if m_desc:
            desc = m_desc.group(1).strip()
            ttype: TaskType = "implementation"
            if pending_type in ("verification", "optional", "implementation"):
                ttype = pending_type  # type: ignore
            else:
                ttype, _ = classify(desc)
            tid = pending_id if pending_id else f"task{idx+1:03d}"
            role = "test" if re.search(r"\btest\b", desc, re.IGNORECASE) else "code"
            tasks.append(AgentTask(id=tid, role=role, description=desc, type=ttype))
            idx += 1
            pending_id = None
            pending_type = None
            i += 1
            continue

        m = re.match(r"^\d+[\.\)]\s*(.+)", stripped)
        if m:
            desc = m.group(1).strip()
            role = "test" if re.search(r"\btest\b", desc, re.IGNORECASE) else "code"
            ttype, required = classify(desc)
            tasks.append(AgentTask(id=f"task{idx+1:03d}", role=role, description=desc, type=ttype, required=required))
            idx += 1
            i += 1
            continue
        if stripped.startswith("- "):
            desc = stripped[2:].strip()
            if desc:
                role = "test" if re.search(r"\btest\b", desc, re.IGNORECASE) else "code"
                ttype, required = classify(desc)
                tasks.append(AgentTask(id=f"task{idx+1:03d}", role=role, description=desc, type=ttype, required=required))
                idx += 1
        i += 1

    if not tasks:
        for line in plan_text.splitlines():
            stripped = line.strip()
            m = re.match(r"^\d+[\.\)]\s*(.+)", stripped)
            if m:
                desc = m.group(1).strip()
                role = "test" if re.search(r"\btest\b", desc, re.IGNORECASE) else "code"
                ttype, required = classify(desc)
                tasks.append(AgentTask(id=f"task{idx+1:03d}", role=role, description=desc, type=ttype, required=required))
                idx += 1
    return tasks


def parse_plan_file(path: Path) -> List[AgentTask]:
    return parse_plan(path.read_text(encoding="utf-8"))
