# Task

Implement `greet(name: str) -> str` in `src/greeting.py`.

Requirements:

- Preserve all existing functions and behavior unless a change is required by this task.
- Trim leading and trailing whitespace from `name`.
- If the trimmed name is empty, raise `ValueError`.
- Otherwise return exactly `Hello, <name>!`.
- Keep the implementation minimal and consistent with the existing codebase.
- Do not make unrelated changes.

Acceptance criteria:

- `greet("Alice")` returns `"Hello, Alice!"`.
- `greet("  Alice  ")` returns `"Hello, Alice!"`.
- `greet("")` raises `ValueError`.
- `greet("   ")` raises `ValueError`.
- Existing repository tests continue to pass.
- The repository remains valid and CI-ready after the task is completed.
