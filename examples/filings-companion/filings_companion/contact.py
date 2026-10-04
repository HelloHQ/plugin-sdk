"""The person-configured contact that goes into the SEC-required User-Agent.

The SEC's fair-access policy (https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
requires automated requests to declare who is making them, for example
"Sample Company Name AdminContact@<sample company domain>.com". This plugin never ships a
contact: the person enters their own, it is stored in plugin storage, and SEC requests are
refused until a valid one exists. Obvious placeholders are rejected.
"""

from __future__ import annotations

import re

from .errors import ContactNotConfigured
from .hostapi import Host

STORAGE_KEY = "contact"
APP_NAME = "HelloHQ-Filings-Companion"
APP_VERSION = "0.1.0"
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+)")
_PLACEHOLDER_WORDS = ("replace", "changeme", "placeholder", "your-", "yourname", "todo", "xxx")
_RESERVED_DOMAINS = ("example.com", "example.org", "example.net")
_RESERVED_SUFFIXES = (".test", ".invalid", ".example", ".localhost", ".local")


def validate_contact(raw: object) -> str:
    """Return the normalised contact string or raise ContactNotConfigured."""
    if not isinstance(raw, str):
        raise ContactNotConfigured("contact must be text such as 'Jane Doe jane@yourdomain.com'")
    if any(ord(c) < 32 or ord(c) == 127 for c in raw):  # blocks header injection outright
        raise ContactNotConfigured("contact contains control characters")
    text = " ".join(raw.split())
    if not 8 <= len(text) <= 120:
        raise ContactNotConfigured("contact must be 8 to 120 characters")
    match = _EMAIL_RE.search(text)
    if not match:
        raise ContactNotConfigured("contact must include your email address")
    domain = match.group(1).lower()
    lowered = text.lower()
    if (
        domain in _RESERVED_DOMAINS
        or domain.endswith(_RESERVED_SUFFIXES)
        or any(w in lowered for w in _PLACEHOLDER_WORDS)
    ):
        raise ContactNotConfigured("contact looks like a placeholder; enter your real contact")
    return text


def user_agent(contact: str) -> str:
    return f"{contact} {APP_NAME}/{APP_VERSION}"


def get_contact(host: Host) -> str:
    """Return the stored, still-valid contact, or raise ContactNotConfigured."""
    stored = host.storage_get(STORAGE_KEY)
    if not stored:
        raise ContactNotConfigured(
            "set a contact first (your name and email): the SEC requires every automated "
            "client to identify itself"
        )
    return validate_contact(stored)


def set_contact(host: Host, raw: object) -> str:
    contact = validate_contact(raw)
    host.storage_set(STORAGE_KEY, contact)
    return contact
