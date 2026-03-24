# Agent Orientation Index

This file helps coding agents and new contributors understand this repository quickly.

## What This Repo Is

SandboxForge MCP is a Python MCP server that orchestrates ephemeral VMs for isolated task execution.

Key point:
- The isolation boundary is the VM.
- Docker/Compose run inside that VM as workload runtime.
- This is VM-contained container execution, not Docker-only host isolation.

## Fast Entry Points

- Project overview: `README.md`
- Setup and prerequisites: `docs/SETUP.md`
- Architecture summary: `docs/PROJECT_STRUCTURE.md`
- Coding constraints: `docs/CODING_STANDARDS.md`

## Core Code Map

- `src/lima_mcp_server/server.py`
  - MCP app construction, transport setup, tool registration.
- `src/lima_mcp_server/service.py`
  - Main orchestration layer for tool behavior and lifecycle logic.
- `src/lima_mcp_server/backend/lima.py`
  - Lima backend adapter (`limactl` operations and VM actions) for macOS/Linux.
- `src/lima_mcp_server/backend/hyperv.py`
  - Hyper-V backend adapter (PowerShell + SSH/SCP operations) for Windows.
- `src/lima_mcp_server/backend/factory.py`
  - Backend selection (`auto|lima|hyperv`) based on host/config.
- `src/lima_mcp_server/runtime.py`
  - Docker and compose command builders/execution helpers.
- `src/lima_mcp_server/workspace_config.py`
  - Workspace/global config schema, defaults, parsing, validation.
- `src/lima_mcp_server/db.py`
  - SQLite persistence for leases and background tasks.
- `src/lima_mcp_server/sweeper.py`
  - TTL-based lease cleanup loop.
- `src/lima_mcp_server/errors.py`
  - Structured error payload shape helpers.

## Primary Tool Flow

1. Validate config and inputs.
2. Create or reuse VM lease.
3. Bootstrap runtime inside VM (including Docker when needed).
4. Sync workspace and run task-oriented tooling.
5. Persist task/lease state in SQLite.
6. Cleanup by TTL sweeper or explicit teardown.

## Test Index

- `tests/test_contract.py`: response contract/payload stability checks.
- `tests/test_service.py`: orchestration and behavior.
- `tests/test_backend.py`: backend adapter expectations.
- `tests/test_runtime_tasks.py`: runtime task behavior.
- `tests/test_workspace_config.py`: config parsing/default/validation.
- `tests/test_db.py`: persistence semantics.
- `tests/test_integration.py`: integration-level behavior.

## MCP/Runtime Expectations

- Host prerequisites include Python 3.11+, `uv`, and backend-specific virtualization tooling.
- Host Docker is optional for VM flows.
- If backend prerequisites are unavailable, VM-backed tools return `BACKEND_UNAVAILABLE`.

## If You Change Behavior

- Keep tool contracts backward compatible where possible.
- Add/adjust tests for any changed payload shape or lifecycle behavior.
- Update both `README.md` and `docs/index.html` when product messaging changes.
