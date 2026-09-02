from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional


TaskType = Literal["implementation", "verification", "optional"]
TaskRole = Literal["code", "test"]
TaskOutcomeStatus = Literal["CHANGED", "SATISFIED", "FAILED"]


@dataclass
class AgentTask:
    id: str
    role: TaskRole
    description: str
    files: List[str] = field(default_factory=list)
    type: TaskType = "implementation"
    required: bool = True
    acceptance: List[str] = field(default_factory=list)
    validation: List[str] = field(default_factory=list)


@dataclass
class TaskOutcome:
    task_id: str
    status: TaskOutcomeStatus
    decision: str = ""
    reason: str = ""
    evidence: List[str] = field(default_factory=list)
    commit_sha: Optional[str] = None
    ci_status: str = ""
    artifacts: List[str] = field(default_factory=list)


@dataclass
class FileSnapshot:
    exists: bool
    content: str = ""
    sha: str = ""


@dataclass
class TaskBaseline:
    commit_sha: str = ""
    files: Dict[str, FileSnapshot] = field(default_factory=dict)


@dataclass
class ToolEvent:
    tool: str
    input: dict = field(default_factory=dict)
    output: str = ""
    exit_code: int = 0


@dataclass
class ExecutionEvidence:
    events: List[ToolEvent] = field(default_factory=list)
    validation: List[ToolEvent] = field(default_factory=list)


AgentExecutionStatus = Literal["COMPLETED", "BUDGET_STOPPED", "ERROR"]


@dataclass
class AgentResult:
    status: Literal["SUCCESS", "FAILED", "INCOMPLETE"]
    message: str = ""
    artifacts: List[str] = field(default_factory=list)
    next_action: str = ""
    commit_message: str = ""
    outcome: Optional[TaskOutcome] = None
    evidence: Optional[ExecutionEvidence] = None
    baseline: Optional[TaskBaseline] = None
    execution_status: AgentExecutionStatus = "COMPLETED"
    stop_reason: Optional[str] = None


def task_baseline_to_dict(baseline: Optional[TaskBaseline]) -> Optional[dict]:
    if baseline is None:
        return None
    files = {}
    for k, snap in (baseline.files or {}).items():
        if isinstance(snap, FileSnapshot):
            files[k] = {"exists": bool(snap.exists), "content": snap.content or "", "sha": snap.sha or ""}
        elif isinstance(snap, dict):
            files[k] = {"exists": bool(snap.get("exists", False)), "content": snap.get("content", "") or "", "sha": snap.get("sha", "") or ""}
        else:
            files[k] = {"exists": False, "content": "", "sha": ""}
    return {"commit_sha": baseline.commit_sha or "", "files": files}


def task_baseline_from_dict(data) -> Optional[TaskBaseline]:
    if not data or not isinstance(data, dict):
        return None
    files = {}
    for k, v in (data.get("files") or {}).items():
        if isinstance(v, dict):
            files[k] = FileSnapshot(exists=bool(v.get("exists", False)), content=v.get("content", "") or "", sha=v.get("sha", "") or "")
        else:
            files[k] = FileSnapshot(exists=False)
    return TaskBaseline(commit_sha=data.get("commit_sha", "") or "", files=files)


def _tool_event_to_dict(ev: ToolEvent) -> dict:
    if isinstance(ev, dict):
        return {"tool": ev.get("tool", "") or "", "input": ev.get("input") or {}, "output": ev.get("output", "") or "", "exit_code": int(ev.get("exit_code", 0) or 0)}
    return {"tool": ev.tool or "", "input": ev.input or {}, "output": ev.output or "", "exit_code": int(ev.exit_code or 0)}


def _tool_event_from_dict(data) -> ToolEvent:
    if not isinstance(data, dict):
        return ToolEvent(tool="", input={}, output="", exit_code=0)
    return ToolEvent(tool=data.get("tool", "") or "", input=data.get("input") or {}, output=data.get("output", "") or "", exit_code=int(data.get("exit_code", 0) or 0))


def execution_evidence_to_dict(evidence: Optional[ExecutionEvidence]) -> Optional[dict]:
    if evidence is None:
        return None
    if isinstance(evidence, dict):
        events = evidence.get("events") or []
        validation = evidence.get("validation") or []
        return {"events": [_tool_event_to_dict(e) for e in events], "validation": [_tool_event_to_dict(e) for e in validation]}
    return {"events": [_tool_event_to_dict(e) for e in (evidence.events or [])], "validation": [_tool_event_to_dict(e) for e in (evidence.validation or [])]}


def execution_evidence_from_dict(data) -> Optional[ExecutionEvidence]:
    if not data or not isinstance(data, dict):
        return None
    events = [_tool_event_from_dict(e) for e in (data.get("events") or [])]
    validation = [_tool_event_from_dict(e) for e in (data.get("validation") or [])]
    return ExecutionEvidence(events=events, validation=validation)
