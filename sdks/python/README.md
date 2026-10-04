# hellohq-plugin-sdk (Python)

Build Tier 1 (Python) HelloHQ plugins. Tier 1 runs inside Pyodide on a Deno
sidecar, so the full scientific stack (NumPy, pandas, scipy, scikit-learn) is
available — desktop only.

## Install

```bash
pip install hellohq-plugin-sdk
```

## Write a plugin

```python
from hellohq_plugin_sdk import serve, UnsupportedFunction

def dispatch(function, args):
    if function == "double":
        return {"value": args[0] * 2}
    raise UnsupportedFunction(function)

serve(dispatch)
```

`serve()` speaks the host's NDJSON protocol on stdin/stdout: it emits `ready`,
answers `ping`, handles `shutdown`, and routes RPC requests to your `dispatch`.
Raise `PluginError(message, code=...)` to return a structured error.

## Data access

The host **pre-fetches** the data your plugin is permitted to read and passes
it in `args` — the sidecar never calls back into the host. Declare the
permissions you need in your manifest (see the registry schema).

## Network fetch

With `network:fetch` (Verified tier) a plugin can call `host.fetch`. The
host only reaches the origins listed on that permission's `scope.origins`
in the manifest, over HTTPS, and does not follow redirects.

```python
from hellohq_plugin_sdk import host

resp = host.fetch("https://api.example.com/rates", headers={"Accept": "application/json"})
resp["status"]          # 200
resp["body"]            # str for a UTF-8 text response, bytes otherwise
resp["body_bytes"]      # always the exact response bytes
resp["body_encoding"]   # "utf8" or "base64": how the host sent it
```

- **Text stays text.** A response whose bytes are valid UTF-8 arrives as a
  `str` in `body`, exactly as in earlier SDK versions.
- **Binary arrives byte-exact.** The host sends any other response (a PDF,
  an image, Latin-1 text) base64-encoded with `body_encoding: "base64"`.
  `fetch` decodes it, so `body` is `bytes`. Code that expects text should
  check `isinstance(resp["body"], str)`. Code that wants bytes either way
  should read `body_bytes`.
- **Unknown `body_encoding`.** If the host sends a value this SDK doesn't
  know, `fetch` raises `PluginError` instead of guessing. Upgrade the SDK if
  you see it.
- **Request headers.** The host forwards only `Accept`, `Accept-Language`,
  `Content-Type`, `If-None-Match` and `If-Modified-Since`
  (`host.ALLOWED_REQUEST_HEADERS`). It drops every other header silently,
  including `Authorization`, `Cookie` and `User-Agent`. Never put a credential
  in a plugin request.
- **Request body.** Send it as text; the host sends its UTF-8 bytes. A body on
  GET or HEAD is refused. Bodies are capped at 8 MiB in each direction.
- **Errors.** Errors raise `PluginError` with the host's `error_code` as
  `code`, e.g. `origin_blocked`, `timeout` or `too_large`. The message never
  contains the URL.

## Test locally

```bash
echo '{"id":1,"function":"double","args":[21]}' | python your_plugin.py
# -> {"type":"ready",...}
#    {"id":1,"result":{"value":42}}
```

Protocol: https://github.com/HelloHQ/plugin-protocol
