# Contributing

Thanks for contributing.

## Before You Start

1. Read the setup guide: [docs/SETUP.md](docs/SETUP.md)
2. Read coding constraints: [docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md)
3. Search existing issues/PRs to avoid duplicate work.

## Development Workflow

1. Create a branch from `main`.
2. Keep changes scoped to a single concern.
3. Add or update tests for behavior changes.
4. Update docs when behavior or APIs change.
5. Open a PR with clear context and validation output.

## Local Validation

Run this before opening a PR:

```bash
uv run pytest -q
```

If your environment does not have optional dependencies, run targeted tests and list what was skipped.

## Pull Request Checklist

- [ ] Tests added or updated for changed behavior
- [ ] Existing tests pass locally
- [ ] Public contract changes documented in `README.md` and/or `docs/`
- [ ] No secrets or machine-specific paths committed
- [ ] Error payload shape remains structured (`error_code`, `message`, `details`)

## Commit Guidance

- Use descriptive commit titles in imperative mood.
- Prefer small commits that are easy to review.
- Do not mix refactors with feature changes unless necessary.

## Review Expectations

PRs are reviewed for:
- Correctness and safety
- Backward compatibility of tool contracts
- Test coverage for regressions
- Maintainable structure and naming

