# Task

Implement `parse_port(value: str) -> int` in `src/config.py`.

Requirements:

- Preserve existing configuration behavior.
- Trim leading and trailing whitespace from the input.
- Convert a valid decimal port string to an integer.
- Valid ports are in the inclusive range `1` through `65535`.
- Raise `ValueError` if:
  - the input is empty or whitespace-only;
  - the input is not a valid decimal integer;
  - the parsed value is outside the valid port range.
- Do not silently clamp invalid values.
- Do not make unrelated changes.
- Keep the implementation minimal and consistent with the existing codebase.

Acceptance criteria:

- `parse_port("8080")` returns `8080`.
- `parse_port(" 443 ")` returns `443`.
- `parse_port("1")` returns `1`.
- `parse_port("65535")` returns `65535`.
- `parse_port("")` raises `ValueError`.
- `parse_port("abc")` raises `ValueError`.
- `parse_port("0")` raises `ValueError`.
- `parse_port("65536")` raises `ValueError`.
- Existing tests continue to pass.
- Full CI must pass before the task is considered complete.
