# Task

Add a `format_user_name(first_name: str, last_name: str) -> str` function to `src/user_utils.py`.

Requirements:

- Preserve existing behavior in `src/user_utils.py`.
- Trim leading and trailing whitespace from both input values.
- Return the normalized full name in the format `<first_name> <last_name>`.
- Raise `ValueError` if either normalized name is empty.
- Do not modify unrelated files unless required for the implementation.
- Keep the implementation consistent with the existing project style.

Acceptance criteria:

- `format_user_name("John", "Smith")` returns `"John Smith"`.
- `format_user_name("  John ", " Smith  ")` returns `"John Smith"`.
- An empty or whitespace-only first name raises `ValueError`.
- An empty or whitespace-only last name raises `ValueError`.
- Existing tests continue to pass.
- The repository remains valid and CI-ready after completion.
