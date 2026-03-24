# SandboxForge MCP

Python MCP server (FastMCP) with dual transports (`stdio` + Streamable HTTP on localhost) and workspace-scoped virtualization lifecycle tools.

## Why This Project

This server provides a predictable local orchestration surface for:
- Ephemeral VM instances (Lima on macOS/Linux, Hyper-V on Windows)
- Workspace sync in/out
- Docker runtime bootstrap inside the VM
- Infra helpers (MySQL/Redis/network setup)
- Docker and docker-compose task execution

## Isolation Model (Important)

SandboxForge is **VM-first isolation**, not Docker-only host isolation.

- Isolation boundary: disposable VM guest
- Workload runtime inside that boundary: Docker/Compose
- Host remains cleaner because Docker engine and app containers run in the VM
- In short: this project creates **VM-contained container runtimes**, not just host Docker namespaces

## Agent Quick Index

Use this map if you are an agent (or a new contributor) trying to understand the repository quickly.

- Primary orientation: [AGENTS.md](AGENTS.md)
- Human setup path: [docs/SETUP.md](docs/SETUP.md)
- Architectural intent: [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)
- Tool registration and transport wiring: [src/lima_mcp_server/server.py](src/lima_mcp_server/server.py)
- Core orchestration and lifecycle rules: [src/lima_mcp_server/service.py](src/lima_mcp_server/service.py)
- Backend execution layers:
  - [src/lima_mcp_server/backend/lima.py](src/lima_mcp_server/backend/lima.py)
  - [src/lima_mcp_server/backend/hyperv.py](src/lima_mcp_server/backend/hyperv.py)
- Docker command builders: [src/lima_mcp_server/runtime.py](src/lima_mcp_server/runtime.py)
- Config schema/defaults/validation: [src/lima_mcp_server/workspace_config.py](src/lima_mcp_server/workspace_config.py)
- Persistence model (leases/tasks): [src/lima_mcp_server/db.py](src/lima_mcp_server/db.py)
- Contract coverage: [tests/test_contract.py](tests/test_contract.py)

## How It Works

1. An MCP client calls a tool exposed by this server (`stdio` or Streamable HTTP).
2. `LeaseService` validates workspace config and enforces lease/task lifecycle rules.
3. `LeaseStore` (SQLite) persists instance leases and background task state.
4. The selected backend executes VM operations (create/start/shell/copy/stop/delete).
5. Docker/Compose commands run inside the leased VM (Docker is runtime, VM is isolation boundary), with optional MySQL/Redis helper setup.
6. A sweeper loop automatically expires and cleans old leases based on TTL.

## System Requirements

Current baseline requirements:
- macOS, Linux, or Windows host
- Python `3.11+`
- [uv](https://github.com/astral-sh/uv)
- Virtualization backend prerequisites for your host:
  - macOS/Linux: Lima (`limactl`)
  - Windows: Hyper-V PowerShell cmdlets + OpenSSH client
- Ability to allocate at least the default VM shape:
  - `1` CPU
  - `2 GiB` RAM
  - `15 GiB` disk

Optional for containerized deployment:
- Docker Engine / Docker Desktop
- `docker compose` (or `docker-compose`)

Important:
- Host VM tooling is not auto-installed by SandboxForge MCP.
- On Linux hosts, ensure Lima prerequisites for your distro are installed (for example QEMU/KVM support).
- On Windows hosts, configure Hyper-V and set `HYPERV_BASE_VHDX`.

## OS Support Status

Status as of **2026-03-24**:

| OS | Status | Notes |
|---|---|---|
| macOS | Supported | Uses Lima backend by default (`vm.vm_type = "vz"`). |
| Linux | Supported | Uses Lima backend by default (`vm.vm_type = "qemu"`). |
| Windows (native) | Supported | Uses Hyper-V backend by default (`SANDBOX_BACKEND=auto`). |
| Windows via WSL2/VM | Not first-class | v1 target is native Windows runtime. |
Unsupported hosts fail backend preflight with `BACKEND_UNAVAILABLE`.

## Quick Start

```bash
uv sync --extra dev
uv run sandboxforge-mcp-server
```

Or use:

```bash
make setup
make run
```

For a full machine setup and troubleshooting flow, see [docs/SETUP.md](docs/SETUP.md).

## Docker Deploy

Build and deploy the MCP server in Docker:

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

This deploy uses:
- `Dockerfile`
- `docker-compose.yml`
- persistent local state mount: `./state:/app/state`
- HTTP exposure on `localhost:8765`

Security default remains loopback-only. Non-loopback bind is now explicitly gated by:
- `MCP_HTTP_ALLOW_NON_LOOPBACK=1`

The compose setup enables this for container usage (`MCP_HTTP_HOST=0.0.0.0`), while non-container runs remain loopback-only by default.

Note:
- Backend lifecycle tools require host-specific virtualization dependencies to be available.
- If backend prerequisites are missing, the server still starts, but VM-backed tools return `BACKEND_UNAVAILABLE`.

## Developer Docs

- Agent-focused repo map: [AGENTS.md](AGENTS.md)
- Setup guide: [docs/SETUP.md](docs/SETUP.md)
- Contribution guide: [CONTRIBUTING.md](CONTRIBUTING.md)
- Coding standards and constraints: [docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md)
- Project layout: [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Security policy: [SECURITY.md](SECURITY.md)

## Workspace Config

Per-workspace config file: `<workspace>/.sandboxforge.toml` (legacy `.orbitforge.toml` and `.lima-mcp.toml` still supported)  
Optional global defaults: `~/.config/sandboxforge-mcp/config.toml` (legacy `~/.config/orbitforge-mcp/config.toml` and `~/.config/lima-mcp/config.toml` still supported)

Precedence:
1. Request overrides
2. Workspace file
3. Global file
4. Built-in defaults

Default VM shape:
- `cpus = 1`
- `memory_gib = 2.0`
- `disk_gib = 15.0`
- `vm_type = "vz"` on macOS, `vm_type = "qemu"` on Linux, `vm_type = null` on other hosts
- `template = "template:docker"`

Infra defaults:
- `infra.ensure_network = true`
- `infra.bridge_to_compose_network = true`
- `infra.include_services_by_default = true`
- `infra.network_name = null` (set this for exact network name without instance suffix)
- `infra.mysql.extra_env = {}`
- `infra.redis.extra_env = {}`
- `infra.mysql.inject_env_to = ".env"`
- `infra.redis.inject_env_to = ".env"`

Notes:
- `prepare_workspace` returns writable workspace path hints in VM (`workspace_paths.preferred`).
- `create_instance(..., wait_for_ready=true)` and `prepare_workspace(..., wait_for_ready=true)` wait for MySQL/Redis readiness.
- `docker_compose` supports `restart`, `stop`, and `exec`, plus logs flags (`follow`, `since`, `tail`).
- `docker_compose` now exports inferred infra connection env (`DB_HOST`, `REDIS_URL`, etc.) during compose commands.
- `docker_build` embeds git/dependency/build metadata labels and supports cache-aware reuse.

## Image Validation and Caching

New tool:
- `validate_image(instance_id, image_name, workspace_root?, checks?)`

`docker_build` rollout:
- Computes workspace state (`git`, dependency hash, content hash)
- Derives expected cached tag from `[build.image_caching]`
- Reuses cached image when validation succeeds
- Falls back to fresh build when stale/missing
- Applies metadata build args and labels automatically

Example config:

```toml
[build.prebuilt]
enabled = true
validation = "strict"

[build.prebuilt.staleness_check]
check_git_commit = true
check_dependencies = true
check_age_threshold = "24h"
on_stale = "rebuild"

[build.image_caching]
enabled = true
strategy = "smart" # content_hash | git_commit | smart
tag_format = "{image}:{git_short}-{deps_hash}"
```

## Environment Variables

- `MCP_HTTP_HOST` (default `127.0.0.1`)
- `MCP_HTTP_ALLOW_NON_LOOPBACK` (default `0`; set `1` to allow hosts like `0.0.0.0`)
- `MCP_HTTP_PORT` (default `8765`)
- `LEASE_DB_PATH` (default `state/leases.db`)
- `SANDBOX_SWEEPER_INTERVAL_SECONDS` (default `60`)
- `SANDBOX_BACKEND` (`auto`, `lima`, `hyperv`; default `auto`)
- `HYPERV_SWITCH_NAME` (default `Default Switch`)
- `HYPERV_BASE_VHDX` (required for Hyper-V backend)
- `HYPERV_STORAGE_DIR` (default `state/hyperv`)
- `HYPERV_SSH_USER` (default `ubuntu`)
- `HYPERV_SSH_KEY_PATH` (optional)
- `HYPERV_SSH_PORT` (default `22`)
- `HYPERV_BOOT_TIMEOUT_SECONDS` (default `180`)

## License

MIT. See [LICENSE](LICENSE).
