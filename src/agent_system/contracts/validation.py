from dataclasses import dataclass, field
from typing import List, Optional, Protocol


@dataclass
class ValidationCommandResult:
    command: str
    exit_code: int
    status: str
    output: str = ""


@dataclass
class ValidationResult:
    tree_sha: str
    status: str
    commands: List[ValidationCommandResult] = field(default_factory=list)


class ValidationRunner(Protocol):
    def validate(self, task, reviewed_tree_sha: str) -> ValidationResult:
        ...
