# AGENTS

## Working Rules
- Make the smallest correct change.
- Prefer `apply_patch` for edits.
- Keep changes scoped to the requested task.
- Do not revert user changes unless explicitly asked.

## Python Style
- Use NumPy-style docstrings for all modules, classes, and functions.
- Keep public APIs explicit and easy to test.
- Prefer clear, small helpers over clever abstractions.

## Quality Gates
- Run `ruff` before finishing any code change.
- Run `ty` on the package when type-related code changes.
- Add or update tests when behavior changes.

## Dependency Management
- Use `uv` for dependency management, environment creation, and tool execution.
- Prefer `uv sync` for installing project dependencies.
- Prefer `uv run` for running project commands, linting, and tests.
- Keep `pyproject.toml` as the source of truth for runtime and dev dependencies.

## DMI GeoParquet Notes
- The repo currently has a planner CLI, a cache builder, and an xarray reader.
- Keep the cache and planner stages resumable and deterministic.
- Preserve the current cache layout unless a change is required by the task.
