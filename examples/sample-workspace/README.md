# Sample Workspace (Real Usage)

This folder is a realistic example workspace for SandboxForge MCP.

It demonstrates:
- A small API app (`app/main.py`) that can talk to MySQL + Redis
- A workspace-level `.sandboxforge.toml`
- Docker and docker-compose flows inside the isolated VM
- End-to-end MCP tool usage for a real project

## Workspace Layout

```text
examples/sample-workspace/
  .sandboxforge.toml
  Dockerfile
  docker-compose.yml
  app/
    main.py
    requirements.txt
```

## What This Example Proves

1. VM lease lifecycle (`create_instance`, `list_instances`, `destroy_instance`)
2. Workspace bootstrap + infra provisioning (`prepare_workspace`)
3. Workspace sync into the guest (`sync_workspace_to_instance`)
4. Build with cache-aware labels (`docker_build`)
5. Compose app startup with injected DB/Redis env (`docker_compose`)
6. Command execution + logs (`run_command`, `docker_logs`, `get_task_logs`)

## Recommended End-to-End Flow

Use absolute paths when calling tools.

Example workspace root (macOS/Linux):
- `/Users/you/.../SandboxMCP/examples/sample-workspace`

Example workspace root (Windows native runtime):
- `C:\\path\\to\\SandboxMCP\\examples\\sample-workspace`

### 1) Validate workspace config

```json
{
  "tool": "validate_workspace_config",
  "workspace_root": "/absolute/path/to/examples/sample-workspace"
}
```

### 2) Create VM instance and auto-bootstrap

```json
{
  "tool": "create_instance",
  "workspace_root": "/absolute/path/to/examples/sample-workspace",
  "ttl_minutes": 60,
  "auto_bootstrap": true,
  "wait_for_ready": true
}
```

Save returned `instance_id`.

### 3) Sync workspace into guest

Use `prepare_workspace` result (`workspace_paths.preferred`) as the VM target path.

```json
{
  "tool": "sync_workspace_to_instance",
  "instance_id": "inst_xxxxxxxx",
  "local_path": "/absolute/path/to/examples/sample-workspace",
  "remote_path": "/workspace/sample-workspace"
}
```

### 4) Build image

```json
{
  "tool": "docker_build",
  "instance_id": "inst_xxxxxxxx",
  "context_path": "/workspace/sample-workspace",
  "image_tag": "sample-workspace-api:dev",
  "dockerfile": "/workspace/sample-workspace/Dockerfile"
}
```

### 5) Start app with compose

```json
{
  "tool": "docker_compose",
  "instance_id": "inst_xxxxxxxx",
  "project_dir": "/workspace/sample-workspace",
  "action": "up",
  "file": "/workspace/sample-workspace/docker-compose.yml",
  "detach": true
}
```

### 6) Check service and dependency connectivity

```json
{
  "tool": "docker_compose",
  "instance_id": "inst_xxxxxxxx",
  "project_dir": "/workspace/sample-workspace",
  "action": "exec",
  "file": "/workspace/sample-workspace/docker-compose.yml",
  "services": ["api"],
  "command": "curl -sS http://localhost:8000/health && echo && curl -sS http://localhost:8000/deps"
}
```

### 7) Tear down

```json
{
  "tool": "destroy_instance",
  "instance_id": "inst_xxxxxxxx",
  "force": false
}
```

## Notes

- `.sandboxforge.toml` in this sample is intentionally opinionated and close to production defaults.
- `prepare_workspace` can write `.env` in this workspace with DB/Redis connection settings.
- The app is intentionally simple; adapt to your actual framework while keeping the same tool flow.
