"""Minimal Tier 1 plugin: doubles a number, echoes a string.

Run it standalone to exercise the protocol:

    echo '{"id":1,"function":"double","args":[21]}' | python examples/echo_plugin.py
"""

from hellohq_plugin_sdk import UnsupportedFunction, serve


def dispatch(function: str, args):
    if function == "double":
        return {"value": args[0] * 2}
    if function == "echo":
        return {"value": args[0]}
    raise UnsupportedFunction(function)


if __name__ == "__main__":
    serve(dispatch)
