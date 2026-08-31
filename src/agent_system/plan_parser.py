import json
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


def _norm_str_list(v) -> List[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def parse_plan_json(data: dict) -> List[AgentTask]:
    tasks_raw = data.get("tasks", []) if isinstance(data, dict) else []
    tasks: List[AgentTask] = []
    for idx, t in enumerate(tasks_raw):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id", f"task{idx+1:03d}")).strip().lower() or f"task{idx+1:03d}"
        desc = str(t.get("description", "")).strip()
        if not desc:
            continue
        ttype = str(t.get("type", "implementation")).strip().lower()
        if ttype not in ("implementation", "verification", "optional"):
            ttype, _ = classify(desc)
        role = str(t.get("role", "")).strip().lower()
        if role not in ("code", "test"):
            role = "test" if re.search(r"\btest\b", desc, re.IGNORECASE) else "code"
        files = _norm_str_list(t.get("files", []))
        acceptance = _norm_str_list(t.get("acceptance", []))
        validation = _norm_str_list(t.get("validation", []))
        required = bool(t.get("required", True)) if ttype != "optional" else False
        tasks.append(AgentTask(id=tid, role=role, description=desc, files=files, type=ttype, required=required, acceptance=acceptance, validation=validation))
    return tasks


def _extract_json(text: str):
    s = text.find("{")
    e = text.rfind("}")
    if s >= 0 and e > s:
        try:
            return json.loads(text[s:e+1])
        except Exception:
            return None
    return None


def render_plan_md(data: dict) -> str:
    objective = str(data.get("objective", "")).strip()
    analysis = str(data.get("analysis", "")).strip()
    risks = _norm_str_list(data.get("risks", []))
    tasks_raw = data.get("tasks", [])
    lines = []
    lines.append(f"# Objective\n\n{objective or '(none)'}\n")
    if analysis:
        lines.append(f"# Analysis\n\n{analysis}\n")
    lines.append("# Tasks\n")
    for t in tasks_raw:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id", "")).strip()
        ttype = str(t.get("type", "implementation")).strip()
        desc = str(t.get("description", "")).strip()
        acc = _norm_str_list(t.get("acceptance", []))
        val = _norm_str_list(t.get("validation", []))
        lines.append(f"ID: {tid}")
        lines.append(f"Type: {ttype}")
        lines.append(f"Description: {desc}")
        if acc:
            lines.append("Acceptance Criteria:")
            for a in acc:
                lines.append(f"- {a}")
        if val:
            lines.append("Validation:")
            for v in val:
                lines.append(f"- {v}")
        lines.append("")
    if risks:
        lines.append("# Risks\n")
        for r in risks:
            lines.append(f"- {r}")
    return "\n".join(lines) + "\n"


def parse_plan(plan_text: str) -> List[AgentTask]:
    j = _extract_json(plan_text)
    if j is not None and isinstance(j.get("tasks"), list) and j["tasks"]:
        parsed = parse_plan_json(j)
        if parsed:
            return parsed

    tasks: List[AgentTask] = []
    idx = 0

    id_re = re.compile(r"^(?:-\s*\*\*)?ID:\*?\*?\s*(.+)", re.IGNORECASE)
    type_re = re.compile(r"^(?:-\s*\*\*)?Type:\*?\*?\s*(.+)", re.IGNORECASE)
    desc_re = re.compile(r"^(?:-\s*\*\*)?Description:\*?\*?\s*(.+)", re.IGNORECASE)

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

        if in_tasks and not id_re.match(stripped) and not type_re.match(stripped) and not desc_re.match(stripped):
            if re.match(r"^- \*\*(Acceptance|Validation)", stripped):
                i += 1
                while i < len(lines):
                    nxt = lines[i].strip()
                    if re.match(r"^(?:-\s*\*\*)?(ID|Type|Description):", nxt, re.IGNORECASE) or re.match(r"^#{1,2}\s+", nxt):
                        break
                    i += 1
                continue
            if re.match(r"^## Task \d+", stripped):
                i += 1
                continue
        i += 1

    if not tasks:
        for line in plan_text.splitlines():
            stripped = line.strip()
            m = re.match(r"^\d+[\.\)]\s*(.+)", stripped)
            if m:
                desc = m.group(1).strip()
                if re.match(r"^(Acceptance|Validation|Risks|Objective|Analysis)", desc):
                    continue
                role = "test" if re.search(r"\btest\b", desc, re.IGNORECASE) else "code"
                ttype, required = classify(desc)
                tasks.append(AgentTask(id=f"task{idx+1:03d}", role=role, description=desc, type=ttype, required=required))
                idx += 1
    return tasks


def load_plan(root: Path = None):
    root = root or Path.cwd()
    jpath = root / ".agent" / "plan.json"
    mpath = root / ".agent" / "plan.md"
    if jpath.exists():
        try:
            data = json.loads(jpath.read_text(encoding="utf-8"))
            tasks = parse_plan_json(data)
            if tasks:
                return data, tasks
        except Exception:
            pass
    if mpath.exists():
        try:
            text = mpath.read_text(encoding="utf-8")
            return None, parse_plan(text)
        except Exception:
            pass
    return None, []


def parse_plan_file(path: Path) -> List[AgentTask]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
            tasks = parse_plan_json(data)
            if tasks:
                return tasks
        except Exception:
            pass
    return parse_plan(text)
