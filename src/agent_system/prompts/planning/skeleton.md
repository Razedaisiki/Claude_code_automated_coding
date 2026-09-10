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

- Decompose based on actual engineering responsibilities in the request and repository.
- A task is warranted only if it has deliverable, independently verifiable content.
- If no further meaningful split exists, return fewer tasks. Do not pad.
- Maximize useful decomposition when genuinely warranted; do not inflate count.
- Split only when the request contains multiple independently understandable, independently reviewable responsibilities.
- Do not merge responsibilities merely because they could be committed together.
- For large multi-module requests, multiple tasks may be warranted, but only as many as justified by distinct responsibilities.
- Do not create procedure tasks (inspect, run tests, review, create directories, think).
- Tests required for an implementation belong to the same task.
- Return only as many skeleton entries as justified, in executable dependency order.
- Never exceed the configured safety limit supplied in the request.
