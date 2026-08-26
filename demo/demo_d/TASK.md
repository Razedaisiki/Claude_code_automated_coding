# Task

Add JSON serialization support for the existing `User` model.

Requirements:

- Preserve the existing `User` model API.
- Add a `to_dict()` method that returns the user's public data as a plain Python dictionary.
- Add a `to_json()` method that returns the same public data as a JSON string.
- The serialized output must include `id`, `name`, and `email`.
- Internal or private attributes must not be exposed.
- JSON output must be valid JSON and represent the same data returned by `to_dict()`.
- Reuse existing project utilities where appropriate instead of duplicating functionality.
- Do not make unrelated changes.

Acceptance criteria:

- `to_dict()` returns the required public fields with their current values.
- `to_json()` can be parsed with `json.loads`.
- Parsing `to_json()` produces the same data as `to_dict()`.
- No private/internal fields are serialized.
- Existing behavior of `User` remains unchanged.
- Existing tests continue to pass.
- Full CI passes.
