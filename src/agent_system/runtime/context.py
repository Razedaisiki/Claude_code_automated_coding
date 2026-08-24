from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from agent_system.agents.models import AgentTask
from agent_system.runtime.git import Git
from agent_system.runtime.tools import TOOLS


@dataclass
class AgentExecutionContext:
    workspace: Path
    task: AgentTask
    git_state: str = ""
    available_tools: List[dict] = field(default_factory=lambda: TOOLS)

    @classmethod
    def from_workspace(cls, workspace: Path, task: AgentTask) -> "AgentExecutionContext":
        git_state = Git(workspace).diff()
        return cls(workspace=workspace, task=task, git_state=git_state)
