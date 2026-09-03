from agent_system.backends.claude_code.backend import (
    ClaudeCodeBackend,
    ClaudeCodeBackend as ClaudeCodeRuntime,
)

from agent_system.runtime.task_baseline import capture_task_baseline

CodingRuntime = ClaudeCodeBackend

__all__ = ["ClaudeCodeRuntime", "ClaudeCodeBackend", "CodingRuntime", "capture_task_baseline"]
