from __future__ import annotations

from typing import Protocol

from agent_system.agents.models import AgentResult, AgentTask, TaskBaseline
from agent_system.context import ProjectContext


class CodingBackend(Protocol):
    def execute(self, task: AgentTask, context: ProjectContext, baseline: TaskBaseline | None = None) -> AgentResult: ...
