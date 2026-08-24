from dataclasses import dataclass, field
from typing import List, Literal


@dataclass
class AgentTask:
    id: str
    role: Literal["code", "test"]
    description: str
    files: List[str] = field(default_factory=list)


@dataclass
class AgentResult:
    status: Literal["SUCCESS", "FAILED"]
    message: str = ""
    artifacts: List[str] = field(default_factory=list)
    next_action: str = ""
