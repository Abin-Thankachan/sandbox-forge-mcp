# Project Structure

```text
src/lima_mcp_server/
  server.py            # MCP app and tool registration
  service.py           # core orchestration and tool behavior
  runtime.py           # docker command builders
  workspace_config.py  # config schema, defaults, and validation
  backend/
    lima.py            # Lima backend implementation
  db.py                # sqlite persistence for leases/tasks
  errors.py            # structured error payload helper
  sweeper.py           # expired lease cleanup loop

tests/
  test_contract.py
  test_service.py
  test_runtime_tasks.py
  test_workspace_config.py
  ...
```

## Design Intent

- Tool contract stability is more important than internal implementation details.
- Backend-specific logic should stay behind backend adapters.
- Runtime command builders should stay side-effect free.

