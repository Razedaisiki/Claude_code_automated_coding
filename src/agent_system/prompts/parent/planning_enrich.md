# Planning Stage: Task Enrichment

Enrich the provided task skeleton entries into fully specified executable tasks.

You will receive a batch of skeleton entries with already-assigned IDs in order.

For each entry, produce:

{
  "id": "task006",
  "role": "code",
  "type": "implementation",
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

- Keep the given IDs exactly; do not rename or invent new IDs.
- Keep task order and responsibility as provided.
- Description describes the completed repository state, not procedure.
- Acceptance describes WHAT must be true, not HOW.
- Validation describes how to verify behavior (concrete, verifiable).
- Implementation task validation belongs to the same task.
- Files is a hint; may be empty if genuinely unknown. Do not hallucinate speculative paths.
- Role must be code for implementation, test for verification.
- Do not add tasks beyond the provided batch.
- Validation must be non-empty for implementation tasks.
