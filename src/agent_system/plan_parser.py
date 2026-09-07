import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

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


def extract_json_object(text: str) -> Optional[dict]:
    if not isinstance(text, str) or not text.strip():
        return None
    decoder = json.JSONDecoder()
    # Find first '{' and try raw_decode from there
    for idx, ch in enumerate(text):
        if ch == "{":
            try:
                obj, end = decoder.raw_decode(text[idx:])
                # Ensure we got a dict
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    return None


def validate_plan_data(data: object, *, max_tasks: int = 32) -> List[str]:
    errors: List[str] = []
    if not isinstance(data, dict):
        return ["plan must be an object"]
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return ["tasks must be a non-empty list"]
    if not tasks:
        return ["tasks must be a non-empty list"]
    if len(tasks) > max_tasks:
        errors.append(f"plan contains {len(tasks)} tasks; configured safety limit is {max_tasks}")
    seen_ids = set()
    for i, task in enumerate(tasks):
        prefix = f"tasks[{i}]"
        if not isinstance(task, dict):
            errors.append(f"{prefix} must be an object")
            continue
        # id
        tid = task.get("id")
        if not isinstance(tid, str) or not tid.strip():
            errors.append(f"{prefix}.id must be a non-empty string")
        else:
            tid_norm = tid.strip().lower()
            if tid_norm in seen_ids:
                errors.append(f"duplicate task id: {tid.strip()}")
            else:
                seen_ids.add(tid_norm)
        # description
        desc = task.get("description")
        if not isinstance(desc, str) or not desc.strip():
            errors.append(f"{prefix}.description must be a non-empty string")
        # acceptance
        acc = task.get("acceptance")
        if not isinstance(acc, list) or not acc:
            errors.append(f"{prefix}.acceptance must contain at least one item")
        elif not all(isinstance(x, str) and x.strip() for x in acc):
            errors.append(f"{prefix}.acceptance must be a list of non-empty strings")
        # validation
        val = task.get("validation")
        if not isinstance(val, list):
            errors.append(f"{prefix}.validation must be a list")
        elif not all(isinstance(x, str) and x.strip() for x in val):
            errors.append(f"{prefix}.validation must be a list of non-empty strings")
        # files
        files = task.get("files", [])
        if not isinstance(files, list):
            errors.append(f"{prefix}.files must be a list")
        elif not all(isinstance(x, str) and x.strip() for x in files):
            errors.append(f"{prefix}.files must be a list of non-empty strings")
        # role
        role = task.get("role", "code")
        if role not in ("code", "test"):
            errors.append(f'{prefix}.role must be "code" or "test"')
        # type
        ttype = task.get("type", "implementation")
        if ttype not in ("implementation", "verification", "optional"):
            errors.append(f'{prefix}.type must be "implementation", "verification" or "optional"')
        # role/type combos
        if isinstance(role, str) and isinstance(ttype, str):
            if ttype == "implementation" and role != "code":
                errors.append(f'{prefix}.role must be "code" for implementation task')
            if ttype == "verification" and role != "test":
                errors.append(f'{prefix}.role must be "test" for verification task')
            if ttype == "verification" and isinstance(val, list) and not val:
                errors.append(f"{prefix}.validation must be non-empty for verification task")
            # strict: implementation should have validation (optional, not enforced to keep mock compat)
            # if ttype == "implementation" and isinstance(val, list) and not val:
            #     errors.append(f"{prefix}.validation must be non-empty for implementation task")
    return errors


def is_valid_plan_data(data: object) -> bool:
    # For backward compat, use default max_tasks from config
    try:
        from agent_system.planning.config import get_planner_config
        max_tasks = get_planner_config().max_tasks
    except Exception:
        max_tasks = 32
    return not validate_plan_data(data, max_tasks=max_tasks)


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
    return extract_json_object(text)


def render_plan_md(data: dict) -> str:
    objective = str(data.get("objective", "")).strip()
    analysis = str(data.get("analysis", "")).strip()
    risks = _norm_str_list(data.get("risks", []))
    tasks_raw = data.get("tasks", [])
    planner_meta = data.get("planner")
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
        role = str(t.get("role", "code")).strip()
        desc = str(t.get("description", "")).strip()
        files = _norm_str_list(t.get("files", []))
        acc = _norm_str_list(t.get("acceptance", []))
        val = _norm_str_list(t.get("validation", []))
        lines.append(f"ID: {tid}")
        lines.append(f"Role: {role}")
        lines.append(f"Type: {ttype}")
        lines.append(f"Description: {desc}")
        if files:
            lines.append("Files:")
            for f in files:
                lines.append(f"- {f}")
        if acc:
            lines.append("Acceptance Criteria:")
            for a in acc:
                lines.append(f"- {a}")
        if val:
            lines.append("Validation:")
            for v in val:
                lines.append(f"- {v}")
        lines.append("")
    if planner_meta and isinstance(planner_meta, dict):
        lines.append("# Planner\n")
        for k, v in planner_meta.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
    if risks:
        lines.append("# Risks\n")
        for r in risks:
            lines.append(f"- {r}")
    return "\n".join(lines) + "\n"


def render_plan_context(plan_data: Optional[dict], *, current_task_id: Optional[str] = None, max_chars: int = 4000) -> str:
    if not isinstance(plan_data, dict):
        return "(no plan)"
    objective = str(plan_data.get("objective", "")).strip()
    tasks_raw = plan_data.get("tasks", [])
    if not isinstance(tasks_raw, list):
        tasks_raw = []
    lines = []
    if objective:
        lines.append(f"Objective: {objective}")
    # compact list
    lines.append(f"Tasks ({len(tasks_raw)}):")
    for t in tasks_raw:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id", "")).strip()
        desc = str(t.get("description", "")).strip()
        marker = " -> current" if tid == current_task_id else ""
        lines.append(f"  {tid}: {desc[:80]}{marker}")
    # current task detail
    if current_task_id:
        for t in tasks_raw:
            if isinstance(t, dict) and str(t.get("id", "")).strip() == current_task_id:
                lines.append("")
                lines.append(f"Current task {current_task_id} details:")
                lines.append(f"  Description: {str(t.get('description','')).strip()[:300]}")
                acc = _norm_str_list(t.get("acceptance", []))
                if acc:
                    lines.append(f"  Acceptance: {acc[0][:120]}")
                val = _norm_str_list(t.get("validation", []))
                if val:
                    lines.append(f"  Validation: {val[0][:120]}")
                break
        # neighbors
        idx = None
        for i, t in enumerate(tasks_raw):
            if isinstance(t, dict) and str(t.get("id","")).strip() == current_task_id:
                idx = i
                break
        if idx is not None:
            if idx > 0:
                prev = tasks_raw[idx-1]
                if isinstance(prev, dict):
                    lines.append(f"Previous: {prev.get('id','')} - {str(prev.get('description','')).strip()[:60]}")
            if idx + 1 < len(tasks_raw):
                nxt = tasks_raw[idx+1]
                if isinstance(nxt, dict):
                    lines.append(f"Next: {nxt.get('id','')} - {str(nxt.get('description','')).strip()[:60]}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...(truncated)"
    return text


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
    role_re = re.compile(r"^(?:-\s*\*\*)?Role:\*?\*?\s*(.+)", re.IGNORECASE)
    desc_re = re.compile(r"^(?:-\s*\*\*)?Description:\*?\*?\s*(.+)", re.IGNORECASE)
    files_header_re = re.compile(r"^(?:-\s*\*\*)?Files\s*:", re.IGNORECASE)
    acc_header_re = re.compile(r"^(?:-\s*\*\*)?(Acceptance(?:\sCriteria)?)\s*:", re.IGNORECASE)
    val_header_re = re.compile(r"^(?:-\s*\*\*)?Validation\s*:", re.IGNORECASE)

    lines = plan_text.splitlines()
    i = 0
    in_tasks = False
    current = None

    def finalize_current():
        nonlocal current, idx, tasks
        if not current:
            return
        desc = str(current.get("description", "")).strip()
        if not desc:
            current = None
            return
        ttype = str(current.get("type", "implementation")).strip().lower()
        if ttype not in ("implementation", "verification", "optional"):
            ttype, _ = classify(desc)
        tid = current.get("id") or f"task{idx+1:03d}"
        tid = str(tid).strip().lower() or f"task{idx+1:03d}"
        role = str(current.get("role", "")).strip().lower()
        if role not in ("code", "test"):
            role = "test" if re.search(r"\btest\b", desc, re.IGNORECASE) else "code"
        files = _norm_str_list(current.get("files", []))
        acceptance = list(current.get("acceptance", []))
        validation = list(current.get("validation", []))
        required = bool(current.get("required", True)) if ttype != "optional" else False
        tasks.append(AgentTask(id=tid, role=role, description=desc, files=files, type=ttype, required=required, acceptance=acceptance, validation=validation))
        idx += 1
        current = None

    while i < len(lines):
        stripped = lines[i].strip()

        if re.match(r"^#{1,2}\s*Tasks\s*$", stripped, re.IGNORECASE):
            finalize_current()
            in_tasks = True
            i += 1
            continue
        if in_tasks and re.match(r"^#{1,2}\s+(Objective|Analysis|Risks|Execution|Planner)", stripped):
            finalize_current()
            break
        if not in_tasks:
            i += 1
            continue

        if current is not None and acc_header_re.match(stripped):
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if not nxt:
                    i += 1
                    continue
                if id_re.match(nxt) or desc_re.match(nxt) or type_re.match(nxt) or role_re.match(nxt) or acc_header_re.match(nxt) or val_header_re.match(nxt) or files_header_re.match(nxt) or re.match(r"^#{1,2}\s+", nxt):
                    break
                m_item = re.match(r"^[-*]\s+(.*)", nxt)
                if m_item:
                    item = m_item.group(1).strip()
                    if item:
                        current["acceptance"].append(item)
                    i += 1
                    continue
                break
            continue

        if current is not None and val_header_re.match(stripped):
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if not nxt:
                    i += 1
                    continue
                if id_re.match(nxt) or desc_re.match(nxt) or type_re.match(nxt) or role_re.match(nxt) or acc_header_re.match(nxt) or val_header_re.match(nxt) or files_header_re.match(nxt) or re.match(r"^#{1,2}\s+", nxt):
                    break
                m_item = re.match(r"^[-*]\s+(.*)", nxt)
                if m_item:
                    item = m_item.group(1).strip()
                    if item:
                        current["validation"].append(item)
                    i += 1
                    continue
                break
            continue

        if current is not None and files_header_re.match(stripped):
            i += 1
            while i < len(lines):
                nxt = lines[i].strip()
                if not nxt:
                    i += 1
                    continue
                if id_re.match(nxt) or desc_re.match(nxt) or type_re.match(nxt) or role_re.match(nxt) or acc_header_re.match(nxt) or val_header_re.match(nxt) or files_header_re.match(nxt) or re.match(r"^#{1,2}\s+", nxt):
                    break
                m_item = re.match(r"^[-*]\s+(.*)", nxt)
                if m_item:
                    item = m_item.group(1).strip()
                    if item:
                        current["files"].append(item)
                    i += 1
                    continue
                break
            continue

        m_id = id_re.match(stripped)
        if m_id:
            if current is not None:
                finalize_current()
            current = {"id": m_id.group(1).strip().lower(), "type": None, "description": "", "acceptance": [], "validation": [], "files": []}
            i += 1
            continue
        m_type = type_re.match(stripped)
        if m_type:
            if current is None:
                current = {"id": None, "type": None, "description": "", "acceptance": [], "validation": [], "files": []}
            current["type"] = m_type.group(1).strip().lower()
            i += 1
            continue
        m_role = role_re.match(stripped)
        if m_role:
            if current is None:
                current = {"id": None, "type": None, "description": "", "acceptance": [], "validation": [], "files": []}
            current["role"] = m_role.group(1).strip().lower()
            i += 1
            continue
        m_desc = desc_re.match(stripped)
        if m_desc:
            if current is None:
                current = {"id": None, "type": None, "description": "", "acceptance": [], "validation": [], "files": []}
            current["description"] = m_desc.group(1).strip()
            i += 1
            continue

        if in_tasks and current is not None and not id_re.match(stripped) and not type_re.match(stripped) and not desc_re.match(stripped):
            if re.match(r"^- \*\*(Acceptance|Validation)", stripped):
                i += 1
                while i < len(lines):
                    nxt = lines[i].strip()
                    if re.match(r"^(?:-\s*\*\*)?(ID|Type|Description|Role|Files):", nxt, re.IGNORECASE) or re.match(r"^#{1,2}\s+", nxt):
                        break
                    i += 1
                continue
            if re.match(r"^## Task \d+", stripped):
                i += 1
                continue
        i += 1

    finalize_current()

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
