# Planning Stage: JSON Repair

The previous JSON response was invalid.

Validation errors:
{{errors}}

Previous raw response:
{{raw}}

Required schema:
{{schema}}

Return corrected JSON only.

Preserve the intended task decomposition. Do not collapse the plan into fewer
tasks merely to repair formatting.

If enriching a batch, preserve all provided task IDs.
If the plan was a skeleton, preserve the intended skeleton count and titles.
