"""Error types. Every failure the core raises is one of these, with a stable code."""

from __future__ import annotations


class PluginCoreError(Exception):
    """Base class. ``code`` is stable and machine-readable; ``message`` is short plain text."""

    code = "error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.message = message


class ValidationError(PluginCoreError):
    """The person's input is not acceptable."""

    code = "invalid_input"


class ContactNotConfigured(PluginCoreError):
    """No (valid) contact has been configured for the SEC User-Agent. SEC calls are refused."""

    code = "contact_not_configured"


class ParseError(PluginCoreError):
    """A source response did not have the documented shape. Never guessed around."""

    code = "unexpected_response_shape"


class OriginNotAllowed(PluginCoreError):
    code = "origin_not_allowed"


class HttpError(PluginCoreError):
    code = "http_error"

    def __init__(self, message: str, *, status: int, code: str | None = None) -> None:
        super().__init__(message, code=code)
        self.status = status


class RateLimited(HttpError):
    """HTTP 429. ``retry_after_s`` is the source's reset hint when it gave one."""

    code = "rate_limited"

    def __init__(self, message: str, *, retry_after_s: int | None = None) -> None:
        super().__init__(message, status=429)
        self.retry_after_s = retry_after_s


class ResponseTooLarge(HttpError):
    code = "response_too_large"
