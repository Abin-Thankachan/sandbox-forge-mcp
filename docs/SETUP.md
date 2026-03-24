# Setup Guide

This guide is the standard setup path for contributors.

## 1. Prerequisites

- macOS or Linux
- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- Lima (`limactl`) in your `PATH`

On macOS (Homebrew):

```bash
brew install uv lima
```

On Linux (example package sources vary by distro):

```bash
# install uv (https://docs.astral.sh/uv/getting-started/installation/)
# install Lima and ensure limactl is in PATH
limactl --version
```

Host defaults:
- macOS uses `vm.vm_type = "vz"` by default.
- Linux uses `vm.vm_type = "qemu"` by default.

Important:
- Host VM tooling is not auto-installed by this project.
- On Linux, install distro-specific Lima prerequisites yourself (including any required QEMU/KVM components).
- This project can auto-install Docker inside the created VM during workspace bootstrap, but it does not provision host virtualization dependencies.

## 2. Clone and Install

```bash
git clone <your-fork-or-repo-url>
cd SandboxMCP
uv sync --dev
```

## 3. Run Tests

```bash
uv run pytest -q
```

If a dependency is missing in your environment, install it via `uv sync --dev` and rerun.

## 4. Run the Server

```bash
uv run sandboxforge-mcp-server
```

## 5. Verify Tooling Quickly

Use a quick smoke flow:

1. `validate_workspace_config` on your workspace
2. `create_instance(..., auto_bootstrap=true, wait_for_ready=true)`
3. `prepare_workspace(..., wait_for_ready=true)` if needed
4. `list_instances` and check `runtime_ready = true`

## 6. Optional: Enable Strict Prebuilt Image Validation

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
- `lima_validate_image(...)` before test runs
- `docker_build(...)` for auto cache hit/miss + metadata labels

## 7. Common Troubleshooting

## `limactl not found in PATH`

- Confirm install: `limactl --version`
- Ensure shell profile exports the correct PATH

## `create_instance` appears hung

- Recent versions include operation timeouts; check the returned structured error.
- Run `list_instances` to verify whether creation succeeded before timeout.

## Docker-compose app cannot reach MySQL/Redis

- Ensure `infra.bridge_to_compose_network = true` in `.sandboxforge.toml`.
- Run compose using the provided `docker_compose` tool (it handles bridge flow).

## Sync permission denied on `/workspace`

- Use `prepare_workspace` output: `workspace_paths.preferred`.
- Default fallback remains `/tmp/workspace`.
