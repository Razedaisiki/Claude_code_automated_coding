# Planning Goal

Create the most useful decomposition of the user's engineering request.

Do not minimize the number of tasks as a goal.

Split work whenever the request contains multiple independently understandable,
independently reviewable engineering responsibilities.

Do not merge two responsibilities merely because they could be delivered in
one Git commit.

Every executable task remains a complete Git/CI delivery boundary.

# Leaf Task Test

A task should remain unsplit only when:

1. It has one primary engineering responsibility.
2. It can reasonably be completed in one focused coding-agent execution.
3. Its acceptance criteria can be evaluated independently.
4. The repository can remain internally consistent and CI-ready after it.
5. A later task does not need to finish the current task's required behavior.
6. Splitting further would mostly create implementation procedure rather than
   independently valuable repository states.

If a task still contains multiple independently understandable and reviewable
responsibilities, split it further.

Example BAD (over-merged):

Implement configuration loading and HTTP client timeout integration

This merges two responsibilities that are independently valuable and should be:

- Implement configuration timeout resolution + corresponding tests
- Integrate resolved timeout into HTTP client + corresponding tests

# Large Requirements

Large multi-module or project-scale requests should normally produce many tasks.

10-25 tasks is reasonable for a genuinely project-scale request when the
engineering boundaries support that decomposition.

This is guidance, not a quota.

A genuinely atomic change may still produce one task.

# Do Not Create Tasks For

- reading files
- repository inspection
- thinking
- running a validation command
- running tests by itself
- reviewing results
- creating a directory needed by implementation
- individual branches or lines inside one cohesive function
- mechanical implementation steps that cannot stand alone

Tests required for an implementation belong to the same implementation task.

Validation belongs inside the same task, not as a separate verification task
unless it is a truly independent non-mutating delivery unit.

# Ordering

Return tasks in executable dependency order.

Earlier tasks may establish infrastructure consumed by later tasks, but every
task must leave the repository valid and CI-ready.

# Task Description Semantics

Executable task descriptions MUST describe the required completed repository state.

Do NOT prescribe repository exploration or implementation procedure.

BAD:

Inspect `src/greeting.py`, understand the existing structure, then add `greet()`.

GOOD:

Implement `greet(name: str) -> str` in `src/greeting.py` with the required normalization and validation behavior.

Do not begin task descriptions with procedural instructions such as:

- Inspect ...
- Read ...
- Check ...
- Explore ...
- First ...
- Then ...

unless the inspection itself is explicitly the user's requested deliverable.

# Output

The detailed JSON schema for each planning stage is described in the
corresponding stage prompt (planning_skeleton.md, planning_enrich.md, etc.).
