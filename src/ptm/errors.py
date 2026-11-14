"""Structured business errors for PTM tools."""

from __future__ import annotations

from typing import Any


class PtmError(Exception):
    code: str = "INTERNAL_ERROR"
    retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


class InvalidArgument(PtmError):
    code = "INVALID_ARGUMENT"
    retryable = False


class NotFound(PtmError):
    code = "NOT_FOUND"
    retryable = False


class Conflict(PtmError):
    code = "CONFLICT"
    retryable = False


class VersionConflict(PtmError):
    code = "VERSION_CONFLICT"
    retryable = True


class DuplicateRequest(PtmError):
    code = "DUPLICATE_REQUEST"
    retryable = False


class PreviewExpired(PtmError):
    code = "PREVIEW_EXPIRED"
    retryable = False


class ConfirmationRequired(PtmError):
    code = "CONFIRMATION_REQUIRED"
    retryable = False


class LimitExceeded(PtmError):
    code = "LIMIT_EXCEEDED"
    retryable = False


class StorageError(PtmError):
    code = "STORAGE_ERROR"
    retryable = True


class TemporaryFailure(PtmError):
    code = "TEMPORARY_FAILURE"
    retryable = True
