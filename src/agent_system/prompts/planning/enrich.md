# Planning Stage: Task Enrichment

Enrich the provided task skeleton entries into fully specified executable tasks.

You will receive a batch of skeleton entries. Each entry has a title and scope.

For each entry, produce:

{
  "description": "...",
  "acceptance": ["...", "..."],
  "validation": ["...", "..."],
  "files": ["..."]
}

Return JSON only:

{
  "tasks": [ ... ]
}

Rules:

- Keep task count exactly the same as input; keep order and responsibility.
- Description describes the completed repository state, not procedure.
- Acceptance describes WHAT must be true, not HOW.
- Validation describes how to verify behavior (concrete, verifiable).
- Implementation task validation belongs to the same task.
- Files is a hint; may be empty if genuinely unknown. Do not hallucinate speculative paths.
- Do not add tasks beyond the provided batch.
- Validation must be non-empty for implementation tasks.
- Do NOT include role, type, or id — they are assigned deterministically by Runtime.
