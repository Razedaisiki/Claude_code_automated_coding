# Planning Stage: Decomposition Refinement

Review the current compact task skeleton.

For every task, determine whether it still contains multiple independent
engineering responsibilities that can each leave the repository CI-ready.

If so, split the broad task further into focused child tasks.

Do not split tasks into procedural steps (inspect, create directories, run tests).

Do not create tasks that are only validation procedure.

Return JSON only:

{
  "changed": true,
  "tasks": [
    {"title": "...", "scope": "..."}
  ]
}

If no further split is warranted:

{
  "changed": false,
  "tasks": [...]
}

Rules:

- Preserve the original objective and task order (split children remain in place of parent).
- Do not inflate task count without independently valuable boundaries.

This stage is split-only.

Do not merge, remove, or consolidate existing responsibilities.

The number of tasks must never decrease.

You may:
- keep a task unchanged
- replace one task with multiple more focused child tasks
