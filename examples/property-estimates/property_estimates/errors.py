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
    """The person's input (or a money value) is not acceptable."""

    code = "invalid_input"


class ParseError(PluginCoreError):
    """A source response did not have the documented shape. Never guessed around."""

    code = "unexpected_response_shape"


class InputTooLarge(PluginCoreError):
    code = "input_too_large"


class OriginNotAllowed(PluginCoreError):
    code = "origin_not_allowed"


class HttpError(PluginCoreError):
    code = "http_error"

    def __init__(self, message: str, *, status: int, code: str | None = None) -> None:
        super().__init__(message, code=code)
        self.status = status


class RateLimited(HttpError):
    code = "rate_limited"


class SourceUnavailable(PluginCoreError):
    """The source has no data for the requested file (for example a year not published)."""

    code = "source_unavailable"


class ProposalRefused(PluginCoreError):
    """The estimate does not meet the rules for proposing a value."""

    code = "proposal_refused"


class PendingHostSupport(PluginCoreError):
    """The host does not offer ``propose`` (it answered ``unknown_method``, or the installed
    SDK predates it). The graceful fallback: nothing was sent, the proposal is returned as data.
    Needs a host with propose-only writes (hellohq with plugins enabled)."""

    code = "pending_host_support"


class ProposeRefused(PluginCoreError):
    """The host refused the whole ``propose`` call; nothing was queued.

    ``code`` is the host's error code (``permission_denied``, ``rate_limit_exceeded``,
    ``quota_exceeded``, ``too_large``, ``bad_request`` ...), ``reason`` its reason code for
    ``bad_request``, ``retryable`` whether a later run can succeed with the same proposal.
    """

    code = "propose_refused"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        reason: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message, code=code)
        self.reason = reason
        self.retryable = retryable
