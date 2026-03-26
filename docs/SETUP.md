# Setup Guide

This guide covers local setup, MCP client connection, first successful flow, and common error rectifications.

## 1. Runtime Requirement

Run the MCP server directly on the host OS (macOS/Linux/Windows native).

Docker-hosted MCP server runtime is not supported for normal operation because host workspace paths and backend virtualization tooling may not be visible in-container.

## 2. Prerequisites

- macOS, Linux, or Windows
- Python 3.11+
- [uv](https://github.com/astral-sh/uv)

Backend prerequisites:
- macOS/Linux: Lima (`limactl`) in `PATH`
- Windows: Hyper-V cmdlets (`New-VM`, `New-VHD`) and OpenSSH client (`ssh`, `scp`)

On macOS (Homebrew):

```bash
brew install uv lima
```

On Linux (package sources vary by distro):

```bash
# install uv (https://docs.astral.sh/uv/getting-started/installation/)
# install Lima and ensure limactl is in PATH
limactl --version
```

On Windows (PowerShell):

```powershell
Get-Command New-VM
Get-Command New-VHD
ssh -V
scp -V
```

Host defaults:
- macOS uses `vm.vm_type = "vz"`
- Linux uses `vm.vm_type = "qemu"`

Important:
- Host VM tooling is not auto-installed by this project.
- Linux users must install distro-specific Lima prerequisites (including required QEMU/KVM packages).
- Windows users must set `HYPERV_BASE_VHDX` to an existing base image path.

## 3. Clone and Install

```bash
git clone <your-fork-or-repo-url>
cd SandboxMCP
uv sync --extra dev
```

## 4. Run the Server Locally (Host Runtime)

```bash
uv run sandboxforge-mcp-server
```

Fallback entrypoint (if script resolution fails):

```bash
uv run python -m lima_mcp_server.server
```

## 5. Connect Your MCP Client

### A. Local stdio connection (recommended)

```json
{
  "mcpServers": {
    "sandboxforge": {
      "command": "uv",
      "args": ["run", "sandboxforge-mcp-server"],
      "cwd": "/absolute/path/to/SandboxMCP"
    }
  }
}
```

If your client shows `Failed to spawn: sandboxforge-mcp-server`:

```json
{
  "mcpServers": {
    "sandboxforge": {
      "command": "uv",
      "args": ["run", "python", "-m", "lima_mcp_server.server"],
      "cwd": "/absolute/path/to/SandboxMCP"
    }
  }
}
```

### B. Streamable HTTP connection

Server endpoint:
- `http://127.0.0.1:8765/mcp`

Config example:

```json
{
  "mcpServers": {
    "sandboxforge-http": {
      "transport": "streamable-http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

Notes:
- `GET /` returns `404` (expected)
- `/mcp` is the MCP endpoint
- Plain curl may show `406 Not Acceptable` unless MCP streamable HTTP headers are used

## 6. First Successful End-to-End Flow

Run this sequence from your MCP client:

1. `validate_workspace_config(workspace_root="/abs/path/to/workspace")`
2. `create_instance(workspace_root="/abs/path/to/workspace", auto_bootstrap=true, wait_for_ready=true)`
3. `prepare_workspace(instance_id="<id>", wait_for_ready=true)`
4. `run_command(instance_id="<id>", command="echo hello-from-sandbox && uname -a")`
5. `destroy_instance(instance_id="<id>")`

Success markers:
- `create_instance` returns an `instance_id`
- `prepare_workspace` returns `runtime_ready = true`
- `run_command` exits with code `0`

## 7. Optional: Strict Prebuilt Image Validation

Create/update `<workspace>/.sandboxforge.toml`:

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
strategy = "content_hash"
tag_format = "{image}:{content_hash}"

[infra]
network_name = "shopify-network" # optional exact name (no instance suffix)
bridge_to_compose_network = true
```

Then use:
- `validate_image(...)` before test runs
- `docker_build(...)` for cache hit/miss + metadata labels

## 8. Common Troubleshooting and Rectification

### `Failed to spawn: sandboxforge-mcp-server`
- Ensure dependencies are installed: `uv sync --extra dev`
- Verify command works in repo root: `uv run sandboxforge-mcp-server --help`
- Use fallback: `uv run python -m lima_mcp_server.server`
- Ensure MCP client `cwd` points to this repository root

### `BACKEND_UNAVAILABLE`
- Verify backend prerequisites for your host
- Confirm `SANDBOX_BACKEND` selection (`auto|lima|hyperv`)
- macOS/Linux: check `limactl --version`
- Windows: check `Get-Command New-VM`, `Get-Command New-VHD`, and OpenSSH availability

### `INSUFFICIENT_HOST_RESOURCES`
- `create_instance` performs host-capacity checks before VM creation (CPU, available memory, free disk)
- Reduce VM shape in `<workspace>/.sandboxforge.toml` (`vm.cpus`, `vm.memory_gib`, `vm.disk_gib`)
- For low-spec machines, prefer `auto_bootstrap=false` and `prepare_workspace(..., include_services=false)`

### Docker-hosted MCP runtime attempted
- Symptom: host workspace paths fail (`/Users/...` not found) or backend is unavailable
- Fix: run server on host OS and reconnect MCP client using local stdio or host HTTP endpoint

### `GET /` works but MCP calls fail
- Use `http://127.0.0.1:8765/mcp`, not root `/`
- Ensure your MCP client is using Streamable HTTP transport

### Old env var still used
- Replace `LIMA_SWEEPER_INTERVAL_SECONDS` with `SANDBOX_SWEEPER_INTERVAL_SECONDS`

### `create_instance` appears hung
- Newer versions include operation timeouts in structured errors
- Run `list_instances` to check whether creation completed

### Dockerized app cannot reach MySQL/Redis
- Set `infra.bridge_to_compose_network = true` in workspace config
- Use provided `docker_compose` tool for compose operations

### Sync permission denied on `/workspace`
- Use `prepare_workspace` output (`workspace_paths.preferred`)
- Fallback path remains `/tmp/workspace`
