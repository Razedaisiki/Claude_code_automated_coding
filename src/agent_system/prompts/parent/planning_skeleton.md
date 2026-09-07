# Planning Stage: Task Skeleton

Generate a compact task skeleton for the engineering request.

Each entry must have only:

- title: short focused engineering responsibility
- scope: one sentence describing the intended completed state

Return JSON only:

{
  "objective": "one paragraph",
  "tasks": [
    {"title": "Create project skeleton and build system", "scope": "Establish the compilable project structure, CLI entry point and build script."}
  ]
}

Rules:

- Maximize useful decomposition. Do not minimize task count as a goal.
- Split whenever the request contains multiple independently understandable, independently reviewable responsibilities.
- Do not merge responsibilities merely because they could be committed together.
- A project-scale request may reasonably require 10-25 or more tasks.
- Do not create procedure tasks (inspect, run tests, review, create directories, think).
- Tests required for an implementation belong to the same task.
- Return 1-32 skeleton entries in executable dependency order.
