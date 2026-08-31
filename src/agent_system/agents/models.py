from dataclasses import dataclass, field
from typing import List, Literal, Optional


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
    files: dict = field(default_factory=dict)


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
