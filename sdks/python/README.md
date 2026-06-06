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

## Test locally

```bash
echo '{"id":1,"function":"double","args":[21]}' | python your_plugin.py
# -> {"type":"ready",...}
#    {"id":1,"result":{"value":42}}
```

Protocol: https://github.com/HelloHQ/plugin-protocol
