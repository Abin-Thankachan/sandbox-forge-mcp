# Coding Standards and Constraints

This project prioritizes stable tool contracts and predictable error behavior.

## Language and Runtime

- Python `>=3.11`
- Type hints for all new public functions/methods
- Keep dependencies minimal and justified

## Architecture Constraints

- `server.py`: tool registration and transport wiring only
- `service.py`: orchestration/business logic and response shaping
- `backend/`: backend-specific command execution (Lima today, extendable to EC2 later)
- `runtime.py`: command construction helpers, no orchestration state
- `workspace_config.py`: schema/defaults/validation

Do not move orchestration logic into `server.py` or backend adapters.

## Error Contract

All failures must return structured payloads via `error_response`:
- `error_code`
- `message`
- `details`

Do not return raw exceptions to callers.

## API Compatibility

- Keep existing tool names and required fields backward compatible.
- When adding fields, prefer additive responses.
- If behavior changes, update `README.md` and tests.

For image workflows:
- Preserve metadata label keys used by validation (`git.commit`, `build.dependencies.hash`, OCI revision/created labels).
- Keep validation payload shape stable (`valid`, `reasons`, `recommendation`, `image_metadata`).

## Testing Rules

- Add tests for new behaviors and edge cases.
- Prefer focused unit tests in `tests/`.
- Contract tests must keep payload keys stable.

## Security and Safety

- Never commit secrets or tokens.
- Avoid shell command construction with untrusted input without quoting.
- Use `shlex.quote` for shell-bound values.
