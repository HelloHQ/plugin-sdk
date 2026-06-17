"""Notes Keeper — Tier-1 Python sidecar (compute half of a WebView plugin)
==========================================================================
Demonstrates ``plugin:storage`` AND a Tier-1 sidecar driving a WebView UI.

Storage is the only way a plugin persists state between runs: a per-plugin,
sandboxed key-value store no other plugin (or the host UI) can read. This
example keeps a run counter and a free-text note.

This is the *compute* half. The *UI* half lives in ``ui/`` (a framework-agnostic
WebView bundle, here vanilla TypeScript) and calls these functions through the
host bridge — see this example's README.

Functions (invoked from the UI via ``host.compute(fn, args)``):
- ``run``        -> ``{"count": N, "note": "..."}``  (the current state)
- ``save_note``  -> ``{"saved": true, "note": "..."}``  (persists the note)

Each returns plain DATA; the WebView renders it. ``save_note`` also pushes a
``note-saved`` event via ``emit_event`` so the UI can react without re-polling —
the same push path Tier-2 plugins use, now available to Tier-1 sidecars.

Sidecar invocation protocol
---------------------------
The host always calls the sidecar's ``run`` function with
``args = {"context": {...}, "input": {"function": <ui-fn>, "args": {...}}}``:
- ``args["context"]`` holds the host's pre-fetched, permission-gated reads,
  keyed by permission id (e.g. ``"read:portfolio_names"``). Denied reads are
  omitted. (Not used here — this plugin reads/writes via live ``host.storage_*``
  RPCs instead.)
- ``args["input"]`` is the UI's ``host.compute(fn, args)`` payload verbatim, so
  we route on ``args["input"]["function"]``.

Permissions required: plugin:storage

Pattern demonstrated
--------------------
- ``host.storage_get(key)`` returns the stored string or ``None``.
- ``host.storage_set(key, value)`` persists a string value.
- ``emit_event(name, payload)`` pushes an event to the WebView.

See: https://hellohq.io/docs/plugins/storage
"""

from __future__ import annotations

from typing import Any

from hellohq_plugin_sdk import UnsupportedFunction, emit_event, host, serve

_COUNT_KEY = "run_count"
_NOTE_KEY = "note"


def dispatch(function: str, args: Any):
    # The host always calls "run"; the UI's intended function + args are nested
    # under args["input"] (see the protocol note in the module docstring).
    inner = (args or {}).get("input") or {}
    fn = inner.get("function", "run")
    fn_args = inner.get("args") or {}

    if fn == "run":
        return _run()
    if fn == "save_note":
        return _save_note(fn_args)
    raise UnsupportedFunction(fn)


def _run() -> dict:
    # Increment the persisted run counter, then return the current state.
    raw = host.storage_get(_COUNT_KEY)
    count = (int(raw) if raw and raw.isdigit() else 0) + 1
    host.storage_set(_COUNT_KEY, str(count))

    note = host.storage_get(_NOTE_KEY) or ""
    return {"count": count, "note": note}


def _save_note(args: dict) -> dict:
    note = str(args.get("note", ""))
    host.storage_set(_NOTE_KEY, note)
    # Push a confirmation event the UI can subscribe to via host.on("note-saved").
    emit_event("note-saved", {"note": note})
    return {"saved": True, "note": note}


if __name__ == "__main__":
    serve(dispatch)
