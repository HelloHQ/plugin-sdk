"""Client-side pre-check of a proposal batch. A convenience, NOT the authority.

The host validates every batch itself (docs/plugin/30 section 3.11) and its
answer is the only one that counts: a batch this module passes can still be
refused or marked ``invalid`` by the host, and one it flags would be refused by
the host too. What this buys a plugin is finding its own bugs (a float amount,
an unknown field, a date-only ``as_of``) in a unit test or before spending one
of its 10 calls a minute, with the same closed reason codes the host uses
(:data:`REASONS`, the ``reason`` enum of ``host-calls.schema.json``).

It mirrors the host's rules as of ``plugin-protocol`` ``propose`` and checks
them in the host's order, reporting the first failing reason per proposal. It
does not check what only the host can know: whether the plugin fetched
``source.origin`` this run, dedup, suppression, bindings, the rate and pending
quotas, or the host's own currency and crypto code lists (``value.currency`` is
only checked against the schema's shape here).

    issues = validate_batch(batch, allowed_kinds={"crypto_ticker"})
    if issues:
        ...
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .proposals import (
    ASSET_KINDS,
    BATCH_SCHEMA,
    MAX_HOLDINGS_PER_BATCH,
    MAX_PAYLOAD_BYTES,
    MAX_PROPOSALS_PER_CALL,
    METHODS,
    PERMISSION_HOLDINGS,
    PERMISSION_VALUATIONS,
)

#: The closed set of reason codes (``reason`` in ``host-calls.schema.json``).
REASONS = frozenset(
    {
        "invalid",
        "too_many",
        "too_large",
        "bad_schema",
        "host_field_supplied",
        "unknown_field",
        "field_not_allowed_for_kind",
        "missing_field",
        "bad_kind",
        "permission_denied",
        "bad_source_key",
        "bad_asset_kind",
        "asset_kind_not_allowed",
        "bad_display_name",
        "bad_instrument",
        "bad_quantity",
        "bad_value",
        "bad_currency",
        "bad_as_of",
        "as_of_in_future",
        "as_of_too_old",
        "bad_method",
        "bad_source",
        "bad_reference",
        "bad_fetched_at",
        "fetched_at_outside_run",
    }
)

#: Names the host adds itself; a batch carrying any of them, at any depth, is
#: refused whole (``host_field_supplied``).
HOST_OWNED_FIELDS = frozenset(
    {
        "plugin_id",
        "plugin_version",
        "content_hash",
        "trust_tier",
        "run_id",
        "received_at",
        "host_observed_origins",
        "host_observed",
        "source_observed",
        "dedup_key",
        "source_digest",
    }
)

MAX_DISPLAY_NAME_LENGTH = 80
MAX_REFERENCE_LENGTH = 200
MAX_SIGNIFICANT_DIGITS = 38
MAX_SCALE = 18
#: ``as_of`` may be at most this far after now.
AS_OF_FUTURE_TOLERANCE = timedelta(minutes=5)
#: ``as_of`` may be at most this many years before now.
AS_OF_MAX_AGE_YEARS = 10
#: Tolerance on each end of the run window for ``source.fetched_at``.
FETCHED_AT_SKEW = timedelta(seconds=5)


@dataclass(frozen=True)
class Issue:
    """One problem found. ``index`` is the proposal's position, or ``None`` for
    a problem with the batch as a whole."""

    index: int | None
    reason: str

    def __str__(self) -> str:
        where = "batch" if self.index is None else f"proposals[{self.index}]"
        return f"{where}: {self.reason}"


def validate_batch(
    batch: Any,
    *,
    now: datetime | None = None,
    granted: Collection[str] | None = None,
    allowed_kinds: Collection[str] | None = None,
    run_start: datetime | None = None,
    run_end: datetime | None = None,
) -> list[Issue]:
    """Pre-check a wire batch (a ``dict``); an empty list means nothing found.

    Args:
        batch: ``{"schema": ..., "proposals": [...]}`` as it would go on the wire.
        now: The clock to judge ``as_of`` against (default: the current time).
        granted: The ``propose:*`` ids this plugin holds. When given, a proposal
            of a kind it has no id for is ``permission_denied``. Omit to skip.
        allowed_kinds: The asset kinds in the permission's ``scope.kinds``. When
            given, a holding outside it is ``asset_kind_not_allowed``.
        run_start: Start of the run. When given, ``source.fetched_at`` must fall
            in ``[run_start, run_end or now]`` (give or take 5 seconds).
        run_end: End of the run (default ``now``).

    Returns:
        A list of :class:`Issue`. A batch-level issue (``index is None``) means
        the host would refuse the whole call; otherwise one issue per proposal
        the host would mark ``invalid``.
    """
    clock = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        return _validate_batch(batch, clock, granted, allowed_kinds, run_start, run_end)
    except Exception:  # noqa: BLE001 - total, like the host's validator
        return [Issue(None, "invalid")]


def validate_proposal(proposal: Any, **options: Any) -> str | None:
    """Pre-check one wire proposal; the first failing reason code, or ``None``.

    Takes the same keyword options as :func:`validate_batch` (without the
    batch-level limits).
    """
    clock = (options.get("now") or datetime.now(UTC)).astimezone(UTC)
    ctx = _Context(
        now=clock,
        future_limit=clock + AS_OF_FUTURE_TOLERANCE,
        oldest=_years_before(clock, AS_OF_MAX_AGE_YEARS),
        fetched_from=(options["run_start"] - FETCHED_AT_SKEW)
        if options.get("run_start")
        else None,
        fetched_to=((options.get("run_end") or clock) + FETCHED_AT_SKEW),
        granted=options.get("granted"),
        allowed_kinds=options.get("allowed_kinds"),
    )
    try:
        _validate_proposal(proposal, ctx)
    except _Reject as rejected:
        return rejected.reason
    except Exception:  # noqa: BLE001
        return "invalid"
    return None


class _Reject(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class _Context:
    now: datetime
    future_limit: datetime
    oldest: datetime
    fetched_from: datetime | None
    fetched_to: datetime
    granted: Collection[str] | None
    allowed_kinds: Collection[str] | None


def _validate_batch(
    batch: Any,
    now: datetime,
    granted: Collection[str] | None,
    allowed_kinds: Collection[str] | None,
    run_start: datetime | None,
    run_end: datetime | None,
) -> list[Issue]:
    if not isinstance(batch, Mapping):
        return [Issue(None, "invalid")]
    shape = _scan(batch)
    if shape.malformed:
        return [Issue(None, "invalid")]
    if shape.bytes > MAX_PAYLOAD_BYTES:
        return [Issue(None, "too_large")]
    if shape.host_field:
        return [Issue(None, "host_field_supplied")]
    for key in batch:
        if key not in ("schema", "proposals"):
            return [Issue(None, "unknown_field")]
    if batch.get("schema") != BATCH_SCHEMA:
        return [Issue(None, "bad_schema")]
    proposals = batch.get("proposals")
    if not isinstance(proposals, list):
        return [Issue(None, "invalid")]
    if len(proposals) > MAX_PROPOSALS_PER_CALL:
        return [Issue(None, "too_many")]
    holdings = sum(
        1 for p in proposals if isinstance(p, Mapping) and p.get("kind") == "holding"
    )
    if holdings > MAX_HOLDINGS_PER_BATCH:
        return [Issue(None, "too_many")]

    ctx = _Context(
        now=now,
        future_limit=now + AS_OF_FUTURE_TOLERANCE,
        oldest=_years_before(now, AS_OF_MAX_AGE_YEARS),
        fetched_from=(run_start.astimezone(UTC) - FETCHED_AT_SKEW)
        if run_start
        else None,
        fetched_to=(run_end.astimezone(UTC) if run_end else now) + FETCHED_AT_SKEW,
        granted=granted,
        allowed_kinds=allowed_kinds,
    )
    issues: list[Issue] = []
    for index, proposal in enumerate(proposals):
        try:
            _validate_proposal(proposal, ctx)
        except _Reject as rejected:
            issues.append(Issue(index, rejected.reason))
        except Exception:  # noqa: BLE001
            issues.append(Issue(index, "invalid"))
    return issues


# ── per proposal ─────────────────────────────────────────────────────────────

_COMMON_KEYS = frozenset(
    {"kind", "source_key", "value", "quantity", "as_of", "method", "source"}
)
_HOLDING_ONLY_KEYS = frozenset({"asset_kind", "display_name", "instrument"})

_SOURCE_KEY = re.compile(r"^[A-Za-z0-9:._/-]{1,256}\Z")
_SYMBOL = re.compile(r"^[A-Za-z0-9:._/-]{1,32}\Z")
_CHAIN = re.compile(r"^[a-z0-9._-]{1,32}\Z")
_FIGI = re.compile(r"^[A-Z0-9]{12}\Z")
_UNIT = re.compile(r"^[A-Za-z0-9._-]{1,16}\Z")
_CURRENCY = re.compile(r"^[A-Z0-9]{3,16}\Z")
_HOST = re.compile(
    r"^(?=.{1,253}\Z)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\Z"
)
_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?\Z")
_RFC3339_UTC = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z\Z", re.ASCII
)
_MAX_DECIMAL_CHARS = 100


def _validate_proposal(raw: Any, ctx: _Context) -> None:
    if not isinstance(raw, Mapping):
        raise _Reject("invalid")
    kind = raw.get("kind")
    if kind not in ("holding", "valuation"):
        raise _Reject("bad_kind")
    if ctx.granted is not None:
        needed = PERMISSION_HOLDINGS if kind == "holding" else PERMISSION_VALUATIONS
        if needed not in ctx.granted:
            raise _Reject("permission_denied")

    for key, value in raw.items():
        if not isinstance(key, str):
            raise _Reject("invalid")
        if key in _COMMON_KEYS:
            continue
        if key in _HOLDING_ONLY_KEYS:
            if kind == "holding" or value is None:
                continue
            raise _Reject("field_not_allowed_for_kind")
        raise _Reject("unknown_field")

    source_key = raw.get("source_key")
    if source_key is None:
        raise _Reject("missing_field")
    if not isinstance(source_key, str) or not _SOURCE_KEY.match(source_key):
        raise _Reject("bad_source_key")

    if kind == "holding":
        _asset_kind(raw.get("asset_kind"), ctx)
        _display_name(raw.get("display_name"))
        _instrument(raw.get("instrument"))

    _quantity(raw.get("quantity"))
    _value(raw.get("value"), required=kind == "valuation")
    _as_of(raw.get("as_of"), ctx)
    _method(raw.get("method"))
    _source(raw.get("source"), ctx)


def _asset_kind(raw: Any, ctx: _Context) -> None:
    if raw is None:
        raise _Reject("missing_field")
    if not isinstance(raw, str) or raw not in ASSET_KINDS:
        raise _Reject("bad_asset_kind")
    if ctx.allowed_kinds is not None and raw not in ctx.allowed_kinds:
        raise _Reject("asset_kind_not_allowed")


def _display_name(raw: Any) -> None:
    if raw is None:
        raise _Reject("missing_field")
    if (
        not isinstance(raw, str)
        or not raw
        or len(raw) > MAX_DISPLAY_NAME_LENGTH  # code points, as the host counts runes
        or raw != raw.strip()
        or not is_plain_display_text(raw)
        or contains_url_like_text(raw, include_bare_domains=True)
    ):
        raise _Reject("bad_display_name")


def _instrument(raw: Any) -> None:
    if raw is None:
        return
    bad = _Reject("bad_instrument")
    if not isinstance(raw, Mapping):
        raise bad
    for key in raw:
        if key not in ("symbol", "chain", "figi"):
            raise bad
    for key, pattern in (("symbol", _SYMBOL), ("chain", _CHAIN), ("figi", _FIGI)):
        value = raw.get(key)
        if value is not None and (
            not isinstance(value, str) or not pattern.match(value)
        ):
            raise bad


def parse_decimal(raw: Any) -> str | None:
    """The canonical text of a wire decimal, or ``None`` if the host would refuse it.

    Digits and at most one point (no sign, exponent, leading zeros, ``.5`` or
    ``5.``); at most 38 significant digits and scale 18, counted without
    trailing fractional zeros. Anything that is not a ``str`` (a JSON number
    included) is refused.
    """
    if (
        not isinstance(raw, str)
        or len(raw) > _MAX_DECIMAL_CHARS
        or not _DECIMAL.match(raw)
    ):
        return None
    integer, _, fraction = raw.partition(".")
    fraction = fraction.rstrip("0")
    if len(fraction) > MAX_SCALE:
        return None
    significant = (
        len(integer) + len(fraction) if integer != "0" else len(fraction.lstrip("0"))
    )
    if significant > MAX_SIGNIFICANT_DIGITS:
        return None
    return f"{integer}.{fraction}" if fraction else integer


def _quantity(raw: Any) -> None:
    if raw is None:
        return
    bad = _Reject("bad_quantity")
    if not isinstance(raw, Mapping):
        raise bad
    for key in raw:
        if key not in ("amount", "unit"):
            raise bad
    unit = raw.get("unit")
    if (
        parse_decimal(raw.get("amount")) is None
        or not isinstance(unit, str)
        or not _UNIT.match(unit)
    ):
        raise bad


def _value(raw: Any, *, required: bool) -> None:
    if raw is None:
        if required:
            raise _Reject("missing_field")
        return
    bad = _Reject("bad_value")
    if not isinstance(raw, Mapping):
        raise bad
    for key in raw:
        if key not in ("amount", "currency"):
            raise bad
    if parse_decimal(raw.get("amount")) is None:
        raise bad
    currency = raw.get("currency")
    if not isinstance(currency, str) or not _CURRENCY.match(currency):
        raise _Reject("bad_currency")


def parse_rfc3339_utc(raw: str) -> datetime | None:
    """Parse RFC 3339 in UTC (``Z``, upper case, ``T`` separator, optional
    fraction of up to nine digits; no offsets, no leap seconds), or ``None``.
    Digits beyond microseconds are dropped. A date that does not exist is ``None``."""
    if not isinstance(raw, str) or len(raw) > 40:
        return None
    match = _RFC3339_UTC.match(raw)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    fraction = (match.group(7) or "").ljust(6, "0")[:6]
    try:
        return datetime(
            year, month, day, hour, minute, second, int(fraction), tzinfo=UTC
        )
    except ValueError:  # month 13, 2026-02-30, hour 24, second 60, year 0
        return None


def _years_before(moment: datetime, years: int) -> datetime:
    try:
        return moment.replace(year=moment.year - years)
    except ValueError:  # 29 February
        return moment.replace(year=moment.year - years, day=28)


def _as_of(raw: Any, ctx: _Context) -> None:
    if raw is None:
        raise _Reject("missing_field")
    parsed = parse_rfc3339_utc(raw) if isinstance(raw, str) else None
    if parsed is None:
        raise _Reject("bad_as_of")
    if parsed > ctx.future_limit:
        raise _Reject("as_of_in_future")
    if parsed < ctx.oldest:
        raise _Reject("as_of_too_old")


def _method(raw: Any) -> None:
    if raw is None:
        return
    if not isinstance(raw, str) or raw not in METHODS:
        raise _Reject("bad_method")


def _source(raw: Any, ctx: _Context) -> None:
    if raw is None:
        raise _Reject("missing_field")
    bad = _Reject("bad_source")
    if not isinstance(raw, Mapping):
        raise bad
    for key in raw:
        if key not in ("origin", "reference", "fetched_at", "price_origin"):
            raise bad
    origin = raw.get("origin")
    reference = raw.get("reference")
    fetched_at = raw.get("fetched_at")
    price_origin = raw.get("price_origin")
    if origin is None or reference is None or fetched_at is None:
        raise _Reject("missing_field")
    if not isinstance(origin, str) or not _HOST.match(origin):
        raise bad
    if price_origin is not None and (
        not isinstance(price_origin, str) or not _HOST.match(price_origin)
    ):
        raise bad
    if (
        not isinstance(reference, str)
        or len(reference) > MAX_REFERENCE_LENGTH
        or not is_plain_display_text(reference)
        or contains_url_like_text(reference, include_bare_domains=False)
    ):
        raise _Reject("bad_reference")
    fetched = parse_rfc3339_utc(fetched_at) if isinstance(fetched_at, str) else None
    if fetched is None:
        raise _Reject("bad_fetched_at")
    if (
        ctx.fetched_from is not None and fetched < ctx.fetched_from
    ) or fetched > ctx.fetched_to:
        raise _Reject("fetched_at_outside_run")


# ── plain text and links ─────────────────────────────────────────────────────


def _forbidden_code_point(c: int) -> bool:
    if c < 0x20 or 0x7F <= c <= 0x9F:  # C0, DEL, C1
        return True
    if c in (0x00A0, 0x00AD, 0x034F, 0x061C, 0x1680, 0x180E, 0x3000, 0xFEFF):
        return True
    return (
        0x2000 <= c <= 0x200F  # spaces, zero width, LRM/RLM
        or 0x2028 <= c <= 0x202F  # separators, embeddings
        or 0x205F <= c <= 0x206F  # math space, invisibles
        or 0xE000 <= c <= 0xF8FF  # private use
        or 0xFFF9 <= c <= 0xFFFC  # annotation, object marker
        or c in (0xFFFE, 0xFFFF)  # non-characters
        or 0x1D173 <= c <= 0x1D17A  # musical formatting
        or 0xE0000 <= c <= 0xE007F  # tag characters
        or c >= 0xF0000  # private use planes
    )


def is_plain_display_text(text: str) -> bool:
    """True when ``text`` is plain text a person can read as written: no control
    characters, line or paragraph breaks, bidirectional controls, invisible
    format characters, private-use characters or unpaired surrogates, and no
    space but U+0020."""
    return not any(
        _forbidden_code_point(ord(ch)) or 0xD800 <= ord(ch) <= 0xDFFF for ch in text
    )


_URL_SCHEME = re.compile(r"[a-z][a-z0-9+.\-]{1,20}://", re.IGNORECASE)
_WWW = re.compile(r"(^|[^a-z0-9])www\.", re.IGNORECASE)
_LINK_SCHEME = re.compile(
    r"(^|[^a-z0-9])(mailto|javascript|vbscript|data|file|tel|sms|blob|http|https|ftp|ws|wss):",
    re.IGNORECASE,
)
_BARE_DOMAIN = re.compile(
    r"(^|[^a-z0-9.-])[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*"
    r"\.(com|net|org|io|co|app|dev|xyz|info|biz|me|ly|to|cc|gg|sh|ai|tv|top|"
    r"site|online|link|click|club|shop|store|tech|cloud|finance|money|wallet|"
    r"crypto|eth|sol|uk|de|fr|jp|cn|ru|sg|hk|tk|ml|ga|cf|gq)"
    r"(?![a-z0-9-])",
    re.IGNORECASE,
)


def contains_url_like_text(text: str, *, include_bare_domains: bool) -> bool:
    """True when ``text`` reads as a link: a ``scheme://`` URL, ``www.``, a link
    scheme such as ``mailto:``, and, with ``include_bare_domains``, a host name
    under a common top-level domain. ``include_bare_domains`` is off for
    ``source.reference``, which may cite a host."""
    return bool(
        _URL_SCHEME.search(text)
        or _WWW.search(text)
        or _LINK_SCHEME.search(text)
        or (include_bare_domains and _BARE_DOMAIN.search(text))
    )


# ── shape scan ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Shape:
    bytes: int = 0
    host_field: bool = False
    malformed: bool = False


def _scan(root: Any) -> _Shape:
    """Depth, JSON size, non-string keys, non-JSON leaves and host-owned names.

    Iterative and bounded so a deep or cyclic structure cannot overflow the
    stack. Size is the length of the compact JSON encoding in UTF-8.
    """
    max_depth = 12
    size = 0
    host_field = False
    stack: list[tuple[Any, int]] = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if size > MAX_PAYLOAD_BYTES:
            break
        if node is None:
            size += 4
        elif isinstance(node, bool):
            size += 4 if node else 5
        elif isinstance(node, (int, float)):
            if isinstance(node, float) and (
                node != node or node in (float("inf"), float("-inf"))
            ):
                return _Shape(malformed=True)
            size += len(json.dumps(node))
        elif isinstance(node, str):
            size += len(node.encode("utf-8", "replace")) + 2
        elif isinstance(node, (list, tuple)):
            if depth >= max_depth:
                return _Shape(malformed=True)
            size += 2 + max(len(node) - 1, 0)
            stack.extend((child, depth + 1) for child in node)
        elif isinstance(node, Mapping):
            if depth >= max_depth:
                return _Shape(malformed=True)
            size += 2 + max(len(node) - 1, 0)
            for key, value in node.items():
                if not isinstance(key, str):
                    return _Shape(malformed=True)
                if key in HOST_OWNED_FIELDS:
                    host_field = True
                size += len(key.encode("utf-8", "replace")) + 3
                stack.append((value, depth + 1))
        else:
            return _Shape(malformed=True)
    return _Shape(bytes=size, host_field=host_field)
