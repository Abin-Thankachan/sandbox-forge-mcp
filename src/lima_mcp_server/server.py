from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Callable

from .backend.factory import build_backend
from .config import ServerConfig
from .db import LeaseStore
from .service import LeaseService
from .sweeper import LeaseSweeper

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _register_tools(app: Any, service: LeaseService) -> None:
    @app.tool()
    def create_instance(
        workspace_root: str,
        ttl_minutes: int | None = None,
        auto_bootstrap: bool = True,
        wait_for_ready: bool = False,
    ) -> dict[str, Any]:
        return service.create_instance(
            workspace_root=workspace_root,
            ttl_minutes=ttl_minutes,
            auto_bootstrap=auto_bootstrap,
            wait_for_ready=wait_for_ready,
        )

    @app.tool()
    def validate_workspace_config(
        workspace_root: str,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.validate_workspace_config(workspace_root=workspace_root, overrides=overrides)

    @app.tool()
    def validate_image(
        instance_id: str,
        image_name: str,
        workspace_root: str | None = None,
        checks: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return service.validate_image(
            instance_id=instance_id,
            image_name=image_name,
            workspace_root=workspace_root,
            checks=checks,
        )

    @app.tool()
    def list_instances(include_expired: bool = False) -> dict[str, Any]:
        return service.list_instances(include_expired=include_expired)

    @app.tool()
    def run_command(instance_id: str, command: str, timeout_seconds: int = 600) -> dict[str, Any]:
        return service.run_command(
            instance_id=instance_id,
            command=command,
            timeout_seconds=timeout_seconds,
        )

    @app.tool()
    def copy_to_instance(instance_id: str, local_path: str, remote_path: str) -> dict[str, Any]:
        return service.copy_to_instance(
            instance_id=instance_id,
            local_path=local_path,
            remote_path=remote_path,
        )

    @app.tool()
    def copy_from_instance(instance_id: str, remote_path: str, local_path: str) -> dict[str, Any]:
        return service.copy_from_instance(
            instance_id=instance_id,
            remote_path=remote_path,
            local_path=local_path,
        )

    @app.tool()
    def destroy_instance(instance_id: str, force: bool = False) -> dict[str, Any]:
        return service.destroy_instance(instance_id=instance_id, force=force)

    @app.tool()
    def prepare_workspace(instance_id: str, include_services: bool = True, wait_for_ready: bool = False) -> dict[str, Any]:
        return service.prepare_workspace(
            instance_id=instance_id,
            include_services=include_services,
            wait_for_ready=wait_for_ready,
        )

    @app.tool()
    def sync_workspace_to_instance(
        instance_id: str,
        local_path: str,
        remote_path: str,
        exclude: list[str] | None = None,
    ) -> dict[str, Any]:
        return service.sync_workspace_to_instance(
            instance_id=instance_id,
            local_path=local_path,
            remote_path=remote_path,
            exclude=exclude,
        )

    @app.tool()
    def sync_instance_to_workspace(
        instance_id: str,
        remote_path: str,
        local_path: str,
    ) -> dict[str, Any]:
        return service.sync_instance_to_workspace(
            instance_id=instance_id,
            remote_path=remote_path,
            local_path=local_path,
        )

    @app.tool()
    def docker_build(
        instance_id: str,
        context_path: str,
        image_tag: str,
        dockerfile: str | None = None,
        build_args: dict[str, str] | None = None,
        target: str | None = None,
        no_cache: bool = False,
    ) -> dict[str, Any]:
        return service.docker_build(
            instance_id=instance_id,
            context_path=context_path,
            image_tag=image_tag,
            dockerfile=dockerfile,
            build_args=build_args,
            target=target,
            no_cache=no_cache,
        )

    @app.tool()
    def docker_run(
        instance_id: str,
        image: str,
        command: str | None = None,
        name: str | None = None,
        env: dict[str, str] | None = None,
        volumes: list[str] | None = None,
        ports: list[str] | None = None,
        workdir: str | None = None,
        detach: bool = True,
        privileged: bool = False,
    ) -> dict[str, Any]:
        return service.docker_run(
            instance_id=instance_id,
            image=image,
            command=command,
            name=name,
            env=env,
            volumes=volumes,
            ports=ports,
            workdir=workdir,
            detach=detach,
            privileged=privileged,
        )

    @app.tool()
    def docker_exec(
        instance_id: str,
        container: str,
        command: str,
        timeout_seconds: int = 600,
    ) -> dict[str, Any]:
        return service.docker_exec(
            instance_id=instance_id,
            container=container,
            command=command,
            timeout_seconds=timeout_seconds,
        )

    @app.tool()
    def docker_logs(
        instance_id: str,
        container: str,
        tail: int = 500,
        follow: bool = False,
        since: str | None = None,
    ) -> dict[str, Any]:
        return service.docker_logs(
            instance_id=instance_id,
            container=container,
            tail=tail,
            follow=follow,
            since=since,
        )

    @app.tool()
    def docker_compose(
        instance_id: str,
        project_dir: str,
        action: str,
        file: str | None = None,
        services: list[str] | None = None,
        detach: bool = True,
        command: str | None = None,
        follow: bool = False,
        since: str | None = None,
        tail: int | None = None,
    ) -> dict[str, Any]:
        return service.docker_compose(
            instance_id=instance_id,
            project_dir=project_dir,
            action=action,
            file=file,
            services=services,
            detach=detach,
            command=command,
            follow=follow,
            since=since,
            tail=tail,
        )

    @app.tool()
    def docker_ps(instance_id: str, all: bool = False) -> dict[str, Any]:
        return service.docker_ps(instance_id=instance_id, all=all)

    @app.tool()
    def docker_images(instance_id: str) -> dict[str, Any]:
        return service.docker_images(instance_id=instance_id)

    @app.tool()
    def docker_cleanup(instance_id: str, mode: str = "safe") -> dict[str, Any]:
        return service.docker_cleanup(instance_id=instance_id, mode=mode)

    @app.tool()
    def start_background_task(
        instance_id: str,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return service.start_background_task(
            instance_id=instance_id,
            command=command,
            cwd=cwd,
            env=env,
        )

    @app.tool()
    def get_task_status(task_id: str) -> dict[str, Any]:
        return service.get_task_status(task_id=task_id)

    @app.tool()
    def get_task_logs(task_id: str, tail: int = 500) -> dict[str, Any]:
        return service.get_task_logs(task_id=task_id, tail=tail)

    @app.tool()
    def stop_task(task_id: str, force: bool = False) -> dict[str, Any]:
        return service.stop_task(task_id=task_id, force=force)

    @app.tool()
    def collect_artifacts(instance_id: str, remote_paths: list[str], local_dest: str) -> dict[str, Any]:
        return service.collect_artifacts(
            instance_id=instance_id,
            remote_paths=remote_paths,
            local_dest=local_dest,
        )

    @app.tool()
    def extend_instance_ttl(instance_id: str, ttl_minutes: int) -> dict[str, Any]:
        return service.extend_instance_ttl(instance_id=instance_id, ttl_minutes=ttl_minutes)


def _build_app(
    service: LeaseService,
    name: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
):
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError("Failed to import FastMCP from mcp SDK") from exc

    app = FastMCP(name=name, host=host, port=port)
    _register_tools(app, service)
    return app


def _run_http(app: Any, host: str, port: int) -> None:
    logger.info("starting streamable-http transport on %s:%s", host, port)
    app.run(transport="streamable-http")


def _safe_run(target: Callable[[], None]) -> None:
    try:
        target()
    except Exception as exc:  # pragma: no cover - defensive runtime logging only
        logger.exception("transport crashed: %s", exc)


def main() -> None:
    _configure_logging()
    config = ServerConfig.from_env()

    try:
        backend = build_backend(config)
    except ValueError as exc:
        logger.error("%s", exc)
        raise

    if not backend.available:
        logger.warning("%s backend unavailable at startup: %s", backend.backend_name, backend.unavailable_reason)
    else:
        logger.info("%s backend ready: %s", backend.backend_name, backend.version)

    store = LeaseStore(config.db_path)
    service = LeaseService(store=store, backend=backend, config=config)

    sweeper = LeaseSweeper(service=service, interval_seconds=config.sweeper_interval_seconds)
    sweeper.start()

    stdio_app = _build_app(service, name="sandboxforge-mcp-stdio")
    http_app = _build_app(
        service,
        name="sandboxforge-mcp-http",
        host=config.http_host,
        port=config.http_port,
    )

    if config.enable_http:
        http_thread = threading.Thread(
            target=_safe_run,
            args=(lambda: _run_http(http_app, config.http_host, config.http_port),),
            name="mcp-http",
            daemon=True,
        )
        http_thread.start()
    else:
        logger.info("streamable-http transport disabled by MCP_ENABLE_HTTP=0")

    logger.info("starting stdio transport")
    stdio_app.run(transport="stdio")


if __name__ == "__main__":
    main()
