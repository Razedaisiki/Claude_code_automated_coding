from dataclasses import dataclass, field
from typing import List, Protocol


@dataclass
class ValidationCheckResult:
    instruction: str
    status: str
    evidence: str = ""


@dataclass
class ValidationResult:
    tree_sha: str
    status: str
    checks: List[ValidationCheckResult] = field(default_factory=list)
    summary: str = ""
    tool_events: List[dict] = field(default_factory=list)


class ValidationRunner(Protocol):
    def validate(self, task, reviewed_tree_sha: str) -> ValidationResult:
        ...
