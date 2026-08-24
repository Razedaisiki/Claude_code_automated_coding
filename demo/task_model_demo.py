from agent_system.agents.models import AgentTask

plan_tasks = [
    AgentTask(id="task001", role="code", description="Implement hello API", files=["src/api.py"]),
    AgentTask(id="task002", role="test", description="Add tests for hello API", files=["tests/test_api.py"]),
]

for t in plan_tasks:
    print(f"{t.id} [{t.role}] {t.description} -> {t.files}")
