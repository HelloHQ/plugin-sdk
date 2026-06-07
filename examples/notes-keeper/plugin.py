"""Finance example: Notes Keeper
=================================
Tier 1 (Python sidecar) plugin demonstrating ``plugin:storage``.

Storage is the only way a plugin can persist state between runs. It is a
per-plugin, sandboxed key-value store — no other plugin (or the host UI) can
read it. This example keeps a simple run counter and a free-text note.

Permissions required: plugin:storage (Tier-1 sidecar only)

Pattern demonstrated
--------------------
- ``host.storage_get(key)`` returns the stored string or ``None``.
- ``host.storage_set(key, value)`` persists a string value.
- ``host.storage_delete(key)`` removes a key.

See: https://hellohq.io/docs/plugins/storage
"""

from __future__ import annotations

from typing import Any

from hellohq_plugin_sdk import UnsupportedFunction, host, serve

_COUNT_KEY = "run_count"
_NOTE_KEY = "note"


def dispatch(function: str, args: Any):
    if function == "run":
        return _run(args or {})
    if function == "save_note":
        host.storage_set(_NOTE_KEY, str((args or {}).get("note", "")))
        return {"kind": "text", "text": "Saved."}
    raise UnsupportedFunction(function)


def _run(args: dict) -> dict:
    # Increment the persisted run counter.
    raw = host.storage_get(_COUNT_KEY)
    count = (int(raw) if raw and raw.isdigit() else 0) + 1
    host.storage_set(_COUNT_KEY, str(count))

    note = host.storage_get(_NOTE_KEY) or "(no note yet)"

    return {
        "kind": "column",
        "children": [
            {"kind": "heading", "text": "Notes Keeper"},
            {"kind": "text", "text": f"You have run this plugin {count} time(s)."},
            {"kind": "key_value_list", "items": [{"key": "Note", "value": note}]},
        ],
    }


if __name__ == "__main__":
    serve(dispatch)
