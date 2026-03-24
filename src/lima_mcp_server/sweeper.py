from __future__ import annotations

import logging
import threading

from .service import LeaseService


class LeaseSweeper:
    def __init__(self, service: LeaseService, interval_seconds: int = 60) -> None:
        self.service = service
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._logger = logging.getLogger(__name__)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="lease-sweeper", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = self.service.expire_expired_leases()
                if result["expired_count"]:
                    self._logger.info(
                        "expired %s lease(s): %s",
                        result["expired_count"],
                        ",".join(result["expired_instance_ids"]),
                    )
                for err in result["errors"]:
                    self._logger.warning("sweeper error: %s", err)
            except Exception as exc:  # pragma: no cover - defensive logging only
                self._logger.exception("unexpected sweeper failure: %s", exc)

            self._stop_event.wait(self.interval_seconds)
