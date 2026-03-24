from __future__ import annotations

import shlex
from textwrap import dedent


class DockerRuntimeAdapter:
    def _docker_prefix(self, docker_command: str | None) -> list[str]:
        return shlex.split(docker_command or "docker")

    def _join(self, docker_command: str | None, tail_args: list[str]) -> str:
        return shlex.join([*self._docker_prefix(docker_command), *tail_args])

    def prepare_runtime_script(self, install_if_missing: bool = True) -> str:
        install_block = dedent(
            """
            if ! command -v docker >/dev/null 2>&1; then
              if command -v apt-get >/dev/null 2>&1; then
                sudo apt-get update
                sudo apt-get install -y docker.io docker-compose-v2 || sudo apt-get install -y docker.io docker-compose-plugin
              elif command -v dnf >/dev/null 2>&1; then
                sudo dnf install -y docker docker-compose-plugin
              else
                echo "No supported package manager found for docker installation" >&2
                exit 2
              fi
            fi
            """
        ).strip()
        skip_install_block = dedent(
            """
            if ! command -v docker >/dev/null 2>&1; then
              echo "Docker binary not found and install_if_missing=false" >&2
              exit 2
            fi
            """
        ).strip()
        chosen_block = install_block if install_if_missing else skip_install_block
        return dedent(
            f"""
            set -e
            {chosen_block}

            sudo systemctl enable --now docker >/dev/null 2>&1 || sudo service docker start >/dev/null 2>&1 || true

            if docker info >/dev/null 2>&1; then
              docker --version
              docker compose version
            elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
              sudo -n docker --version
              sudo -n docker compose version
            else
              echo "Docker daemon is not reachable with current permissions" >&2
              exit 3
            fi
            """
        ).strip()

    def docker_build_command(
        self,
        context_path: str,
        image_tag: str,
        docker_command: str | None = None,
        dockerfile: str | None = None,
        build_args: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
        target: str | None = None,
        no_cache: bool = False,
    ) -> str:
        args = ["build", "-t", image_tag]
        if dockerfile:
            args.extend(["-f", dockerfile])
        if build_args:
            for key, value in build_args.items():
                args.extend(["--build-arg", f"{key}={value}"])
        if labels:
            for key, value in labels.items():
                args.extend(["--label", f"{key}={value}"])
        if target:
            args.extend(["--target", target])
        if no_cache:
            args.append("--no-cache")
        args.append(context_path)
        return self._join(docker_command, args)

    def docker_run_command(
        self,
        image: str,
        docker_command: str | None = None,
        command: str | None = None,
        name: str | None = None,
        env: dict[str, str] | None = None,
        volumes: list[str] | None = None,
        ports: list[str] | None = None,
        workdir: str | None = None,
        detach: bool = True,
        privileged: bool = False,
    ) -> str:
        args = ["run"]
        if detach:
            args.append("-d")
        if name:
            args.extend(["--name", name])
        if privileged:
            args.append("--privileged")
        if workdir:
            args.extend(["-w", workdir])
        if env:
            for key, value in env.items():
                args.extend(["-e", f"{key}={value}"])
        if volumes:
            for item in volumes:
                args.extend(["-v", item])
        if ports:
            for item in ports:
                args.extend(["-p", item])

        args.append(image)
        if command:
            args.extend(["sh", "-lc", command])
        return self._join(docker_command, args)

    def docker_exec_command(self, container: str, command: str, docker_command: str | None = None) -> str:
        return self._join(docker_command, ["exec", container, "sh", "-lc", command])

    def docker_logs_command(
        self,
        container: str,
        docker_command: str | None = None,
        tail: int = 500,
        follow: bool = False,
        since: str | None = None,
    ) -> str:
        args = ["logs", "--tail", str(tail)]
        if follow:
            args.append("--follow")
        if since:
            args.extend(["--since", since])
        args.append(container)
        return self._join(docker_command, args)

    def docker_compose_command(
        self,
        action: str,
        docker_command: str | None = None,
        file: str | None = None,
        services: list[str] | None = None,
        detach: bool = True,
        command: str | None = None,
        follow: bool = False,
        since: str | None = None,
        tail: int | None = None,
        quiet: bool = False,
    ) -> str:
        args = ["compose"]
        if file:
            args.extend(["-f", file])

        normalized = action.strip().lower()
        args.append(normalized)
        if normalized == "up" and detach:
            args.append("-d")

        if normalized == "logs":
            if follow:
                args.append("--follow")
            if since:
                args.extend(["--since", since])
            if tail is not None:
                args.extend(["--tail", str(tail)])

        if normalized == "ps" and quiet:
            args.append("-q")

        if services and normalized in {"up", "ps", "logs", "pull", "build", "restart", "stop"}:
            args.extend(services)

        if normalized == "exec":
            if not services:
                raise ValueError("compose exec requires at least one service")
            args.extend(["-T", services[0], "sh", "-lc", command or ""])

        return self._join(docker_command, args)

    def docker_ps_command(self, all_containers: bool = False, docker_command: str | None = None) -> str:
        args = ["ps"]
        if all_containers:
            args.append("-a")
        return self._join(docker_command, args)

    def docker_images_command(self, docker_command: str | None = None) -> str:
        return self._join(docker_command, ["images"])

    def docker_cleanup_command(self, mode: str = "safe", docker_command: str | None = None) -> str:
        normalized = mode.strip().lower()
        if normalized == "aggressive":
            return " && ".join(
                [
                    self._join(docker_command, ["container", "prune", "-f"]),
                    self._join(docker_command, ["image", "prune", "-af"]),
                    self._join(docker_command, ["network", "prune", "-f"]),
                    self._join(docker_command, ["volume", "prune", "-f"]),
                ]
            )
        return " && ".join(
            [
                self._join(docker_command, ["container", "prune", "-f"]),
                self._join(docker_command, ["image", "prune", "-f"]),
            ]
        )
