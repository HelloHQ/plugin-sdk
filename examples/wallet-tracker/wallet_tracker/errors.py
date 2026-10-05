"""Error types shared by the pure core.

Every failure mode of the core is one of these, each with a stable ``code`` so
callers (and tests) never have to match on message text.
"""

from __future__ import annotations


class CoreError(Exception):
    """Base class. ``code`` is stable; ``message`` is human-readable."""

    code = "error"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class ValidationError(CoreError):
    """An input (address, request, proposal) failed validation."""

    code = "invalid_input"


class ParseError(CoreError):
    """A vendor response did not have the documented shape.

    Parsers never guess: a missing or mistyped field raises this.
    """

    code = "parse_error"


class HostError(CoreError):
    """The host could not perform an operation."""

    code = "host_error"


class HostUnsupported(HostError):
    """The host does not offer the operation at all (e.g. ``propose``)."""

    code = "unsupported"


class ProposeRefused(HostError):
    """The host refused a whole ``propose`` call; nothing in it was queued.

    ``code`` is the host's error code (``permission_denied``,
    ``rate_limit_exceeded``, ``quota_exceeded``, ``too_large``, ...);
    ``reason`` is its reason code for ``bad_request``. ``retryable`` says a
    later run can succeed with the same batch.
    """

    code = "propose_refused"

    def __init__(
        self, message: str, code: str | None = None, *, reason: str | None = None, retryable: bool = False
    ) -> None:
        super().__init__(message, code)
        self.reason = reason
        self.retryable = retryable


class FetchError(HostError):
    """A fetch was denied, failed in transport, or returned a bad status."""

    code = "fetch_error"


class RateLimitedError(FetchError):
    """The source kept answering 429/5xx after all backoff retries."""

    code = "rate_limited"


class ResponseTooLarge(FetchError):
    """A response body exceeded the configured cap."""

    code = "response_too_large"
