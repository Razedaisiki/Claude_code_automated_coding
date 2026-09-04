from agent_system.runtime.state_store import CURRENT_SCHEMA_VERSION, ALLOWED_STATUSES, StateManager
from agent_system.runtime.checkpoint import Checkpoint as _Checkpoint


class CompatStateManager(StateManager):
    def validate(self) -> None:
        _Checkpoint(self.root).validate()


StateManager = CompatStateManager  # type: ignore

__all__ = ["StateManager", "CompatStateManager", "CURRENT_SCHEMA_VERSION", "ALLOWED_STATUSES"]
