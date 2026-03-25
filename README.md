# SandboxForge MCP

SandboxForge MCP is a Python MCP server for **VM-first isolated execution** with workspace-aware tooling.

It supports multiple virtualization backends:
- **Lima** on macOS and Linux
- **Hyper-V** on native Windows

Inside each VM, it can prepare and run Docker/Compose workloads, manage supporting services (MySQL/Redis), and orchestrate task execution with lease-based lifecycle controls.

## Table of Contents
- [What It Provides](#what-it-provides)
- [Isolation Model](#isolation-model)
- [OS Support](#os-support)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Tool Surface](#tool-surface)
- [Configuration](#configuration)
- [Environment Variables](#environment-variables)
- [Windows Notes](#windows-notes)
- [Docker Deployment](#docker-deployment)
- [Development](#development)
- [Migration Notes (v1 Breaking)](#migration-notes-v1-breaking)
- [Troubleshooting](#troubleshooting)
- [Related Docs](#related-docs)
- [License](#license)

## What It Provides
- Ephemeral VM lifecycle orchestration (`create/list/run/copy/destroy`)
- Workspace bootstrap and sync in/out
- Docker runtime preparation inside guest VM
- Docker / docker-compose execution helpers
- Optional infra setup (network, MySQL, Redis)
- Background task management and artifact collection
- Lease persistence and TTL-based cleanup via SQLite
- Image validation and cache-aware build flow

## Isolation Model
SandboxForge is **VM-first isolation**, not Docker-on-host isolation.

- Isolation boundary: disposable VM guest
- Runtime inside boundary: Docker/Compose
- Result: stronger host separation and reduced host pollution

## OS Support
Status as of **March 25, 2026**:

| Host OS | Status | Backend | Notes |
|---|---|---|---|
| macOS | Supported | Lima | Default `vm.vm_type = "vz"` |
| Linux | Supported | Lima | Default `vm.vm_type = "qemu"` |
| Windows (native) | Supported | Hyper-V | Requires Hyper-V + `HYPERV_BASE_VHDX` + OpenSSH client |
| Windows (WSL2-hosted server runtime) | Not supported in v1 | N/A | v1 target is native Windows server runtime |

Unsupported hosts or missing backend prerequisites return `BACKEND_UNAVAILABLE`.

## Architecture
High-level flow:
1. MCP client invokes server tool (`stdio` or Streamable HTTP)
2. `LeaseService` validates request/config and applies lifecycle rules
3. `LeaseStore` persists leases/tasks in SQLite
4. Selected backend executes VM operations
5. Docker/Compose operations run inside guest VM
6. Sweeper expires stale leases by TTL

Key modules:
- `src/lima_mcp_server/server.py`: MCP app + tool registration
- `src/lima_mcp_server/service.py`: orchestration and response shaping
- `src/lima_mcp_server/backend/lima.py`: Lima backend
- `src/lima_mcp_server/backend/hyperv.py`: Hyper-V backend
- `src/lima_mcp_server/backend/factory.py`: backend selection (`auto|lima|hyperv`)
- `src/lima_mcp_server/workspace_config.py`: workspace/global config parsing and validation
- `src/lima_mcp_server/runtime.py`: Docker/Compose command builders
- `src/lima_mcp_server/db.py`: SQLite lease/task persistence

## Requirements
- Python `3.11+`
- [`uv`](https://github.com/astral-sh/uv)
- Host virtualization prerequisites:
  - macOS/Linux: `limactl` available in `PATH`
  - Windows: Hyper-V PowerShell cmdlets + `ssh`/`scp`

Minimum default VM shape:
- `cpus = 1`
- `memory_gib = 2.0`
- `disk_gib = 15.0`

## Quick Start
```bash
uv sync --extra dev
uv run sandboxforge-mcp-server
```

Using Make:
```bash
make setup
make run
```

## Tool Surface
Registered MCP tools include:
- `create_instance`
- `validate_workspace_config`
- `validate_image`
- `list_instances`
- `run_command`
- `copy_to_instance`
- `copy_from_instance`
- `destroy_instance`
- `prepare_workspace`
- `sync_workspace_to_instance`
- `sync_instance_to_workspace`
- `docker_build`
- `docker_run`
- `docker_exec`
- `docker_logs`
- `docker_compose`
- `docker_ps`
- `docker_images`
- `docker_cleanup`
- `start_background_task`
- `get_task_status`
- `get_task_logs`
- `stop_task`
- `collect_artifacts`
- `extend_instance_ttl`

## Configuration
Workspace config files (precedence high -> low):
1. Request overrides
2. `<workspace>/.sandboxforge.toml`
3. `~/.config/sandboxforge-mcp/config.toml`
4. Built-in defaults

Legacy config files still supported:
- Workspace: `.orbitforge.toml`, `.lima-mcp.toml`
- Global: `~/.config/orbitforge-mcp/config.toml`, `~/.config/lima-mcp/config.toml`

Default VM config:
- `template = "template:docker"`
- `vm_type = "vz"` on macOS
- `vm_type = "qemu"` on Linux
- `vm_type = null` on other hosts

For full schema and examples, see `docs/SETUP.md` and `src/lima_mcp_server/workspace_config.py`.

## Environment Variables
Core server:
- `MCP_HTTP_HOST` (default `127.0.0.1`)
- `MCP_HTTP_ALLOW_NON_LOOPBACK` (default `0`)
- `MCP_HTTP_PORT` (default `8765`)
- `MCP_ENABLE_HTTP` (default `1`)
- `LEASE_DB_PATH` (default `state/leases.db`)
- `MAX_INSTANCES` (default `3`)
- `DEFAULT_TTL_MINUTES` (default `30`)
- `MAX_TTL_MINUTES` (default `120`)
- `SANDBOX_SWEEPER_INTERVAL_SECONDS` (default `60`)

Backend selection:
- `SANDBOX_BACKEND` (`auto`, `lima`, `hyperv`; default `auto`)

Hyper-V backend:
- `HYPERV_SWITCH_NAME` (default `Default Switch`)
- `HYPERV_BASE_VHDX` (**required** for Hyper-V)
- `HYPERV_STORAGE_DIR` (default `state/hyperv`)
- `HYPERV_SSH_USER` (default `ubuntu`)
- `HYPERV_SSH_KEY_PATH` (optional)
- `HYPERV_SSH_PORT` (default `22`)
- `HYPERV_BOOT_TIMEOUT_SECONDS` (default `180`)

## Windows Notes
- v1 supports **native Windows host runtime**, not WSL2-hosted server runtime
- `HYPERV_BASE_VHDX` must reference an existing base image
- OpenSSH client (`ssh`, `scp`) must be available in `PATH`
- On native Windows runtime, `workspace_root` must be a Windows path

## Docker Deployment
Build and run via helper script:
```bash
./scripts/docker-deploy.sh deploy
```

Or with Make:
```bash
make docker-up
```

Useful operations:
```bash
./scripts/docker-deploy.sh logs
./scripts/docker-deploy.sh ps
./scripts/docker-deploy.sh down
```

## Development
Run tests:
```bash
uv run pytest -q
```

Integration test gates:
- Lima: `RUN_LIMA_INTEGRATION=1`
- Hyper-V: `RUN_HYPERV_INTEGRATION=1` (Windows host)

## Migration Notes (v1 Breaking)
This project now uses backend-neutral API naming.

Breaking changes from pre-v1:
- Tool rename: `lima_validate_image` -> `validate_image`
- Response/storage field rename: `lima_name` -> `backend_instance_name`
- Error code rename: `LIMA_COMMAND_FAILED` -> `BACKEND_COMMAND_FAILED`
- Env var rename: `LIMA_SWEEPER_INTERVAL_SECONDS` -> `SANDBOX_SWEEPER_INTERVAL_SECONDS`

## Troubleshooting
- `BACKEND_UNAVAILABLE`:
  - Verify backend prerequisites for your host
  - Confirm selected backend via `SANDBOX_BACKEND`
- Linux Lima failures around KVM/QEMU:
  - Install host virtualization dependencies required by Lima
- Windows Hyper-V failures:
  - Confirm `Get-Command New-VM` and `Get-Command New-VHD`
  - Confirm `HYPERV_BASE_VHDX` exists and is readable

## Related Docs
- Setup: `docs/SETUP.md`
- Project structure: `docs/PROJECT_STRUCTURE.md`
- Coding constraints: `docs/CODING_STANDARDS.md`
- Contributor guide: `CONTRIBUTING.md`
- Changelog: `CHANGELOG.md`
- Security: `SECURITY.md`
- Agent orientation: `AGENTS.md`

## License
MIT. See `LICENSE`.
