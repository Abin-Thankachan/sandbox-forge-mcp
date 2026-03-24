# Changelog

All notable changes to this project are documented in this file.

## [Unreleased] - 2026-03-24

### Added
- New `lima_validate_image` tool for prebuilt image validation against workspace state.
- Build caching and staleness configuration:
  - `[build.prebuilt]`
  - `[build.prebuilt.staleness_check]`
  - `[build.image_caching]`
  - `[build.staleness_detection]`
- Cache-aware `docker_build` flow with workspace fingerprinting and metadata labels.
- Compose enhancements:
  - additional actions: `restart`, `stop`, `exec`
  - logs flags: `follow`, `since`, `tail`
- Network and env improvements:
  - `infra.network_name` for exact network naming
  - compose-time connection env export (`DB_HOST`, `REDIS_URL`, etc.)
  - `.env` upsert merge behavior for injected keys

### Changed
- Improved workspace config validation errors to include allowed keys.
- `create_instance` / `prepare_workspace` support readiness waits for infra services.
- Rebranded project/tool name to **SandboxForge MCP**:
  - package metadata and primary CLI command now use `sandboxforge-mcp-server`
  - new preferred config files: `.sandboxforge.toml` and `~/.config/sandboxforge-mcp/config.toml`
  - legacy `.orbitforge.toml`, `.lima-mcp.toml`, `~/.config/orbitforge-mcp/config.toml`, and `~/.config/lima-mcp/config.toml` remain supported

### Fixed
- Fixed hard failure when `beautifulsoup4` is missing:
  - `keyword_scraper` now has a standard-library HTML parser fallback.
  - Test suite no longer fails at import time without `bs4`.
