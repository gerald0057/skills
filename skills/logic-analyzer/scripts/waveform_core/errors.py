from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolError(Exception):
    code: str
    message: str
    stage: str
    details: dict[str, Any] = field(default_factory=dict)
    hint: str | None = None
    retryable: bool = False
    exit_code: int = 1

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.details:
            result["details"] = self.details
        if self.hint:
            result["hint"] = self.hint
        return result


def invalid_request(message: str, **details: Any) -> ToolError:
    return ToolError(
        "INVALID_REQUEST",
        message,
        "request",
        details,
        exit_code=2,
    )
