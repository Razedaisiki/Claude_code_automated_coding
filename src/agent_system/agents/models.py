from dataclasses import dataclass, field
from typing import List, Literal


TaskType = Literal["implementation", "verification", "optional"]
TaskRole = Literal["code", "test"]


@dataclass
class AgentTask:
    id: str
    role: TaskRole
    description: str
    files: List[str] = field(default_factory=list)
    type: TaskType = "implementation"
    required: bool = True
    acceptance: List[str] = field(default_factory=list)


@dataclass
class AgentResult:
    status: Literal["SUCCESS", "FAILED"]
    message: str = ""
    artifacts: List[str] = field(default_factory=list)
    next_action: str = ""
    commit_message: str = ""
