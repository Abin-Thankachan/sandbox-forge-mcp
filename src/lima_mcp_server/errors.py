from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolFailure(Exception):
    error_code: str
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details or {},
        }
        return payload


def error_response(error_code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return ToolFailure(error_code=error_code, message=message, details=details).to_dict()
