from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:  # pragma: no cover - py>=3.11 path
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:  # pragma: no cover - py<3.11 fallback
    import tomli as tomllib  # type: ignore[no-redef]


DEFAULT_CONFIG: dict[str, Any] = {
    "vm": {
        "cpus": 1,
        "memory_gib": 2.0,
        "disk_gib": 15.0,
        "arch": None,
        "vm_type": "vz",
        "template": "template:docker",
    },
    "docker": {
        "install_if_missing": True,
    },
    "build": {
        "enable_buildkit": True,
        "min_free_gib": 3.0,
        "prebuilt": {
            "enabled": True,
            "validation": "strict",
            "staleness_check": {
                "check_git_commit": True,
                "check_dependencies": True,
                "check_age_threshold": "24h",
                "on_stale": "rebuild",
            },
        },
        "image_caching": {
            "enabled": True,
            "strategy": "smart",
            "tag_format": "{image}:{git_short}-{deps_hash}",
        },
        "staleness_detection": {
            "check_git_commit": True,
            "check_git_dirty": True,
            "check_dependencies": ["pyproject.toml", "uv.lock"],
            "check_dockerfile": True,
            "max_age_threshold": "24h",
            "on_stale": "rebuild",
            "on_dirty_workspace": "warn_and_rebuild",
            "on_missing": "build",
        },
    },
    "infra": {
        "ensure_network": True,
        "bridge_to_compose_network": True,
        "include_services_by_default": True,
        "network_name": None,
        "network_name_prefix": "lima-net",
        "mysql": {
            "enabled": True,
            "image": "mysql:8.0",
            "root_password": "root",
            "database": "app",
            "container_name_prefix": "lima-mysql",
            "extra_env": {},
            "inject_env_to": ".env",
        },
        "redis": {
            "enabled": True,
            "image": "redis:7-alpine",
            "container_name_prefix": "lima-redis",
            "extra_env": {},
            "inject_env_to": ".env",
        },
    },
    "sync": {
        "include_git": False,
        "exclude_patterns": [
            ".venv",
            ".venv/**",
            ".pytest_cache",
            ".pytest_cache/**",
            ".uv-cache",
            ".uv-cache/**",
            "__pycache__",
            "__pycache__/**",
        ],
    },
}


class WorkspaceConfigError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("workspace config invalid")
        self.errors = errors


@dataclass(frozen=True)
class VmSettings:
    cpus: int
    memory_gib: float
    disk_gib: float
    arch: str | None
    vm_type: str | None
    template: str


@dataclass(frozen=True)
class DockerSettings:
    install_if_missing: bool


@dataclass(frozen=True)
class BuildSettings:
    enable_buildkit: bool
    min_free_gib: float
    prebuilt: "BuildPrebuiltSettings"
    image_caching: "BuildImageCachingSettings"
    staleness_detection: "BuildStalenessDetectionSettings"


@dataclass(frozen=True)
class BuildPrebuiltStalenessCheckSettings:
    check_git_commit: bool
    check_dependencies: bool
    check_age_threshold_hours: float | None
    on_stale: str


@dataclass(frozen=True)
class BuildPrebuiltSettings:
    enabled: bool
    validation: str
    staleness_check: BuildPrebuiltStalenessCheckSettings


@dataclass(frozen=True)
class BuildImageCachingSettings:
    enabled: bool
    strategy: str
    tag_format: str


@dataclass(frozen=True)
class BuildStalenessDetectionSettings:
    check_git_commit: bool
    check_git_dirty: bool
    check_dependencies: list[str]
    check_dockerfile: bool
    max_age_threshold_hours: float | None
    on_stale: str
    on_dirty_workspace: str
    on_missing: str


@dataclass(frozen=True)
class MysqlSettings:
    enabled: bool
    image: str
    root_password: str
    database: str
    container_name_prefix: str
    extra_env: dict[str, str]
    inject_env_to: str | None


@dataclass(frozen=True)
class RedisSettings:
    enabled: bool
    image: str
    container_name_prefix: str
    extra_env: dict[str, str]
    inject_env_to: str | None


@dataclass(frozen=True)
class InfraSettings:
    ensure_network: bool
    bridge_to_compose_network: bool
    include_services_by_default: bool
    network_name: str | None
    network_name_prefix: str
    mysql: MysqlSettings
    redis: RedisSettings


@dataclass(frozen=True)
class SyncSettings:
    include_git: bool
    exclude_patterns: list[str]


@dataclass(frozen=True)
class WorkspaceSettings:
    workspace_root: str
    workspace_id: str
    vm: VmSettings
    docker: DockerSettings
    build: BuildSettings
    infra: InfraSettings
    sync: SyncSettings
    sources: list[str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def normalize_workspace_root(workspace_root: str) -> Path:
    return Path(workspace_root).expanduser().resolve()


def derive_workspace_id(workspace_root: str) -> str:
    normalized = str(normalize_workspace_root(workspace_root))
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
    return f"ws_{digest[:12]}"


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = _deep_copy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = _deep_copy(value)
    return out


def _load_toml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_bytes()
    parsed = tomllib.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise WorkspaceConfigError([f"{path}: top-level TOML value must be a table"])
    return parsed


def _expect_table(parent: dict[str, Any], key: str, errors: list[str]) -> dict[str, Any]:
    value = parent.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f"{key} must be a table")
        return {}
    return value


def _require_allowed_keys(name: str, table: dict[str, Any], allowed: set[str], errors: list[str]) -> None:
    unknown = sorted(set(table.keys()) - allowed)
    allowed_list = ", ".join(sorted(allowed))
    for key in unknown:
        errors.append(f"{name}.{key} is not supported (allowed: {allowed_list})")


def _expect_bool(table: dict[str, Any], key: str, errors: list[str]) -> bool:
    value = table.get(key)
    if isinstance(value, bool):
        return value
    errors.append(f"{key} must be a boolean")
    return False


def _expect_positive_float(table: dict[str, Any], key: str, errors: list[str]) -> float:
    value = table.get(key)
    if isinstance(value, int):
        value = float(value)
    if isinstance(value, float) and value > 0:
        return value
    errors.append(f"{key} must be a positive number")
    return 0.0


def _expect_positive_int(table: dict[str, Any], key: str, errors: list[str]) -> int:
    value = table.get(key)
    if isinstance(value, int) and value > 0:
        return value
    errors.append(f"{key} must be a positive integer")
    return 1


def _expect_string(table: dict[str, Any], key: str, errors: list[str], allow_none: bool = False) -> str | None:
    value = table.get(key)
    if allow_none and value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value
    errors.append(f"{key} must be a non-empty string")
    return None


def _expect_string_list(table: dict[str, Any], key: str, errors: list[str]) -> list[str]:
    value = table.get(key)
    if not isinstance(value, list):
        errors.append(f"{key} must be an array of strings")
        return []
    invalid = [item for item in value if not isinstance(item, str)]
    if invalid:
        errors.append(f"{key} must only contain strings")
        return []
    return [item for item in value]


def _expect_string_map(table: dict[str, Any], key: str, errors: list[str]) -> dict[str, str]:
    value = table.get(key)
    if not isinstance(value, dict):
        errors.append(f"{key} must be a table of string values")
        return {}

    output: dict[str, str] = {}
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str):
            errors.append(f"{key} must only contain string keys and string values")
            return {}
        output[k] = v
    return output


def _expect_enum(
    table: dict[str, Any],
    key: str,
    allowed: set[str],
    errors: list[str],
) -> str:
    value = table.get(key)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in allowed:
            return normalized
    errors.append(f"{key} must be one of: {', '.join(sorted(allowed))}")
    return sorted(allowed)[0]


def _expect_duration_hours(
    table: dict[str, Any],
    key: str,
    errors: list[str],
    allow_none: bool = False,
) -> float | None:
    value = table.get(key)
    if allow_none and value is None:
        return None

    if isinstance(value, (int, float)) and float(value) >= 0:
        return float(value)

    if isinstance(value, str):
        raw = value.strip().lower()
        if not raw:
            errors.append(f"{key} must be a non-empty duration")
            return None
        try:
            if raw.endswith("h"):
                return float(raw[:-1])
            if raw.endswith("m"):
                return float(raw[:-1]) / 60.0
            if raw.endswith("d"):
                return float(raw[:-1]) * 24.0
        except ValueError:
            errors.append(f"{key} has invalid duration value '{value}'")
            return None
        errors.append(f"{key} must use h/m/d suffix (for example: 24h, 30m, 7d)")
        return None

    errors.append(f"{key} must be a number of hours or duration string")
    return None


def _validate_workspace_config(merged: dict[str, Any], workspace_root: str, sources: list[str]) -> WorkspaceSettings:
    errors: list[str] = []
    _require_allowed_keys("root", merged, {"vm", "docker", "build", "infra", "sync"}, errors)

    vm_table = _expect_table(merged, "vm", errors)
    _require_allowed_keys("vm", vm_table, {"cpus", "memory_gib", "disk_gib", "arch", "vm_type", "template"}, errors)
    vm = VmSettings(
        cpus=_expect_positive_int(vm_table, "cpus", errors),
        memory_gib=_expect_positive_float(vm_table, "memory_gib", errors),
        disk_gib=_expect_positive_float(vm_table, "disk_gib", errors),
        arch=_expect_string(vm_table, "arch", errors, allow_none=True),
        vm_type=_expect_string(vm_table, "vm_type", errors, allow_none=True),
        template=_expect_string(vm_table, "template", errors) or "template:docker",
    )

    docker_table = _expect_table(merged, "docker", errors)
    _require_allowed_keys("docker", docker_table, {"install_if_missing"}, errors)
    docker = DockerSettings(
        install_if_missing=_expect_bool(docker_table, "install_if_missing", errors),
    )

    build_table = _expect_table(merged, "build", errors)
    _require_allowed_keys(
        "build",
        build_table,
        {"enable_buildkit", "min_free_gib", "prebuilt", "image_caching", "staleness_detection"},
        errors,
    )
    prebuilt_table = _expect_table(build_table, "prebuilt", errors)
    _require_allowed_keys("build.prebuilt", prebuilt_table, {"enabled", "validation", "staleness_check"}, errors)
    prebuilt_staleness_table = _expect_table(prebuilt_table, "staleness_check", errors)
    _require_allowed_keys(
        "build.prebuilt.staleness_check",
        prebuilt_staleness_table,
        {"check_git_commit", "check_dependencies", "check_age_threshold", "on_stale"},
        errors,
    )
    prebuilt_staleness = BuildPrebuiltStalenessCheckSettings(
        check_git_commit=_expect_bool(prebuilt_staleness_table, "check_git_commit", errors),
        check_dependencies=_expect_bool(prebuilt_staleness_table, "check_dependencies", errors),
        check_age_threshold_hours=_expect_duration_hours(prebuilt_staleness_table, "check_age_threshold", errors, allow_none=True),
        on_stale=_expect_enum(prebuilt_staleness_table, "on_stale", {"rebuild", "warn", "fail"}, errors),
    )
    prebuilt = BuildPrebuiltSettings(
        enabled=_expect_bool(prebuilt_table, "enabled", errors),
        validation=_expect_enum(prebuilt_table, "validation", {"strict", "warn", "disabled"}, errors),
        staleness_check=prebuilt_staleness,
    )

    image_caching_table = _expect_table(build_table, "image_caching", errors)
    _require_allowed_keys("build.image_caching", image_caching_table, {"enabled", "strategy", "tag_format"}, errors)
    image_caching = BuildImageCachingSettings(
        enabled=_expect_bool(image_caching_table, "enabled", errors),
        strategy=_expect_enum(image_caching_table, "strategy", {"content_hash", "git_commit", "smart"}, errors),
        tag_format=_expect_string(image_caching_table, "tag_format", errors) or "{image}:{git_short}-{deps_hash}",
    )

    staleness_detection_table = _expect_table(build_table, "staleness_detection", errors)
    _require_allowed_keys(
        "build.staleness_detection",
        staleness_detection_table,
        {
            "check_git_commit",
            "check_git_dirty",
            "check_dependencies",
            "check_dockerfile",
            "max_age_threshold",
            "on_stale",
            "on_dirty_workspace",
            "on_missing",
        },
        errors,
    )
    staleness_detection = BuildStalenessDetectionSettings(
        check_git_commit=_expect_bool(staleness_detection_table, "check_git_commit", errors),
        check_git_dirty=_expect_bool(staleness_detection_table, "check_git_dirty", errors),
        check_dependencies=_expect_string_list(staleness_detection_table, "check_dependencies", errors),
        check_dockerfile=_expect_bool(staleness_detection_table, "check_dockerfile", errors),
        max_age_threshold_hours=_expect_duration_hours(staleness_detection_table, "max_age_threshold", errors, allow_none=True),
        on_stale=_expect_enum(staleness_detection_table, "on_stale", {"rebuild", "warn", "fail"}, errors),
        on_dirty_workspace=_expect_enum(
            staleness_detection_table,
            "on_dirty_workspace",
            {"warn_and_rebuild", "rebuild", "warn", "fail"},
            errors,
        ),
        on_missing=_expect_enum(staleness_detection_table, "on_missing", {"build", "warn", "fail"}, errors),
    )
    build = BuildSettings(
        enable_buildkit=_expect_bool(build_table, "enable_buildkit", errors),
        min_free_gib=_expect_positive_float(build_table, "min_free_gib", errors),
        prebuilt=prebuilt,
        image_caching=image_caching,
        staleness_detection=staleness_detection,
    )

    infra_table = _expect_table(merged, "infra", errors)
    _require_allowed_keys(
        "infra",
        infra_table,
        {
            "ensure_network",
            "bridge_to_compose_network",
            "include_services_by_default",
            "network_name",
            "network_name_prefix",
            "mysql",
            "redis",
        },
        errors,
    )

    mysql_table = _expect_table(infra_table, "mysql", errors)
    _require_allowed_keys(
        "infra.mysql",
        mysql_table,
        {"enabled", "image", "root_password", "database", "container_name_prefix", "extra_env", "inject_env_to"},
        errors,
    )
    mysql = MysqlSettings(
        enabled=_expect_bool(mysql_table, "enabled", errors),
        image=_expect_string(mysql_table, "image", errors) or "mysql:8.0",
        root_password=_expect_string(mysql_table, "root_password", errors) or "root",
        database=_expect_string(mysql_table, "database", errors) or "app",
        container_name_prefix=_expect_string(mysql_table, "container_name_prefix", errors) or "lima-mysql",
        extra_env=_expect_string_map(mysql_table, "extra_env", errors),
        inject_env_to=_expect_string(mysql_table, "inject_env_to", errors, allow_none=True),
    )

    redis_table = _expect_table(infra_table, "redis", errors)
    _require_allowed_keys("infra.redis", redis_table, {"enabled", "image", "container_name_prefix", "extra_env", "inject_env_to"}, errors)
    redis = RedisSettings(
        enabled=_expect_bool(redis_table, "enabled", errors),
        image=_expect_string(redis_table, "image", errors) or "redis:7-alpine",
        container_name_prefix=_expect_string(redis_table, "container_name_prefix", errors) or "lima-redis",
        extra_env=_expect_string_map(redis_table, "extra_env", errors),
        inject_env_to=_expect_string(redis_table, "inject_env_to", errors, allow_none=True),
    )

    infra = InfraSettings(
        ensure_network=_expect_bool(infra_table, "ensure_network", errors),
        bridge_to_compose_network=_expect_bool(infra_table, "bridge_to_compose_network", errors),
        include_services_by_default=_expect_bool(infra_table, "include_services_by_default", errors),
        network_name=_expect_string(infra_table, "network_name", errors, allow_none=True),
        network_name_prefix=_expect_string(infra_table, "network_name_prefix", errors) or "lima-net",
        mysql=mysql,
        redis=redis,
    )

    sync_table = _expect_table(merged, "sync", errors)
    _require_allowed_keys("sync", sync_table, {"include_git", "exclude_patterns"}, errors)
    sync = SyncSettings(
        include_git=_expect_bool(sync_table, "include_git", errors),
        exclude_patterns=_expect_string_list(sync_table, "exclude_patterns", errors),
    )

    if errors:
        raise WorkspaceConfigError(errors)

    return WorkspaceSettings(
        workspace_root=str(normalize_workspace_root(workspace_root)),
        workspace_id=derive_workspace_id(workspace_root),
        vm=vm,
        docker=docker,
        build=build,
        infra=infra,
        sync=sync,
        sources=sources,
    )


def resolve_workspace_settings(
    workspace_root: str,
    overrides: dict[str, Any] | None = None,
    global_config_path: Path | None = None,
) -> WorkspaceSettings:
    root = normalize_workspace_root(workspace_root)
    if not root.exists():
        raise WorkspaceConfigError([f"workspace_root does not exist: {root}"])
    if not root.is_dir():
        raise WorkspaceConfigError([f"workspace_root must be a directory: {root}"])

    global_paths: list[Path]
    if global_config_path is not None:
        global_paths = [global_config_path]
    else:
        global_paths = [
            Path.home() / ".config" / "lima-mcp" / "config.toml",
            Path.home() / ".config" / "orbitforge-mcp" / "config.toml",
            Path.home() / ".config" / "sandboxforge-mcp" / "config.toml",
        ]
    workspace_paths = [root / ".lima-mcp.toml", root / ".orbitforge.toml", root / ".sandboxforge.toml"]

    merged = _deep_copy(DEFAULT_CONFIG)
    sources = ["defaults"]

    for global_path in global_paths:
        try:
            global_cfg = _load_toml_file(global_path)
        except (OSError, tomllib.TOMLDecodeError, WorkspaceConfigError) as exc:
            raise WorkspaceConfigError([f"failed to parse {global_path}: {exc}"]) from exc
        if global_cfg:
            merged = _deep_merge(merged, global_cfg)
            sources.append(str(global_path))

    for workspace_path in workspace_paths:
        try:
            workspace_cfg = _load_toml_file(workspace_path)
        except (OSError, tomllib.TOMLDecodeError, WorkspaceConfigError) as exc:
            raise WorkspaceConfigError([f"failed to parse {workspace_path}: {exc}"]) from exc
        if workspace_cfg:
            merged = _deep_merge(merged, workspace_cfg)
            sources.append(str(workspace_path))

    if overrides:
        if not isinstance(overrides, dict):
            raise WorkspaceConfigError(["overrides must be a table/object"])
        merged = _deep_merge(merged, overrides)
        sources.append("request_overrides")

    return _validate_workspace_config(merged=merged, workspace_root=str(root), sources=sources)
