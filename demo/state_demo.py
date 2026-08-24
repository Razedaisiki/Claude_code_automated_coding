import json
import tempfile
from pathlib import Path

from agent_system.init import init_workspace
from agent_system.supervisor.state import StateManager

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    init_workspace(root)
    sm = StateManager(root)

    print("Before:")
    print(json.dumps(sm.load(), indent=2))

    sm.update(status="RUNNING")

    print("\nAfter:")
    print(json.dumps(sm.load(), indent=2))

    assert sm.load()["status"] == "RUNNING"
    print("\nState updated successfully")
