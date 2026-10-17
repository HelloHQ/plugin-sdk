"""The pure UI builder and the dispatch around it (no process, no host)."""

from __future__ import annotations

import json

import pytest
from hellohq_plugin_sdk.protocol import UnsupportedFunction

from hello_calvinx import MAX_TABLE_ROWS, dispatch, names_from_context, portfolio_names_ui

#: Every node type the app's declarative renderer draws
#: (hellohq lib/app/presentation/widgets/plugin/declarative/plugin_declarative_renderer.dart).
RENDERED = {
    "column",
    "row",
    "section",
    "heading",
    "text",
    "divider",
    "key-value-list",
    "table",
    "metric",
    "chart",
    "button",
    "select",
    "badge-row",
    "empty-state",
    "loading",
}
HEADING = {"type": "heading", "level": 1, "text": "Hello, Calvin!"}


def _assert_renderable(node, depth=1):
    assert isinstance(node, dict) and node.get("type") in RENDERED, node
    assert depth <= 6, "over the renderer's maxDepth"
    kids = node.get("children", [])
    assert len(kids) <= 50, "over the renderer's maxChildren"
    for kid in kids:
        _assert_renderable(kid, depth + 1)


def _table_names(doc):
    (table,) = [c for c in doc["children"] if c["type"] == "table"]
    assert table["columns"] == [{"key": "name", "label": "Portfolio"}]
    return [row["name"] for row in table["rows"]]


def test_n_names_render_a_heading_summary_and_one_column_table():
    names = ["Personal", "Business", "Retirement"]
    doc = portfolio_names_ui(names)
    _assert_renderable(doc)
    assert doc["type"] == "column"
    assert doc["children"][0] == HEADING
    assert doc["children"][1] == {
        "type": "text",
        "color": "muted",
        "content": "You have 3 portfolios in this workspace.",
    }
    assert _table_names(doc) == names
    assert all(c["type"] != "empty-state" for c in doc["children"])


def test_one_name_uses_the_singular():
    doc = portfolio_names_ui(["Solo"])
    assert doc["children"][1]["content"] == "You have 1 portfolio in this workspace."
    assert _table_names(doc) == ["Solo"]


def test_zero_names_render_the_no_portfolios_empty_state():
    doc = portfolio_names_ui([])
    _assert_renderable(doc)
    assert doc["children"][0] == HEADING
    assert [c["type"] for c in doc["children"]] == ["heading", "empty-state"]
    assert doc["children"][1]["title"] == "No portfolios yet"


def test_refusal_renders_the_unavailable_empty_state():
    doc = portfolio_names_ui(None)
    _assert_renderable(doc)
    assert [c["type"] for c in doc["children"]] == ["heading", "empty-state"]
    assert doc["children"][1]["title"] == "Portfolio names unavailable"


def test_hostile_names_round_trip_exactly_through_json():
    names = [
        'quote " and backslash \\',
        "line\nbreak\ttab\r",
        "ctl \x00\x01\x1f",
        "unicode 日本語 € 🚀",
        "<b>x</b>",
        "",
    ]
    doc = json.loads(json.dumps(portfolio_names_ui(names)))
    assert _table_names(doc) == names


def test_rows_are_capped_at_the_renderer_limit_with_a_note():
    names = [str(i) for i in range(MAX_TABLE_ROWS + 7)]
    doc = portfolio_names_ui(names)
    _assert_renderable(doc)
    assert _table_names(doc) == names[:MAX_TABLE_ROWS]
    assert doc["children"][-1]["content"] == "Showing the first 500 of 507 portfolios."
    assert portfolio_names_ui(names[:MAX_TABLE_ROWS])["children"][-1]["type"] == "table"


def test_the_ui_builder_does_not_mutate_its_input():
    names = ["B", "A"]
    portfolio_names_ui(names)
    assert names == ["B", "A"]


@pytest.mark.parametrize(
    ("context", "want"),
    [
        (
            {"read:portfolio_names": [{"id": "p1", "name": "Personal"}, {"id": "p2", "name": "Business"}]},
            ["Personal", "Business"],
        ),
        ({"read:portfolio_names": []}, []),
        ({}, None),  # not granted / refused: the host omits the key
        (None, None),
        ({"read:portfolio_names": "nope"}, None),
        ({"read:portfolio_names": [{"id": "p1"}, {"name": 7}, "Plain", {"name": "Ok"}]}, ["Plain", "Ok"]),
    ],
)
def test_names_from_context(context, want):
    assert names_from_context(context) == want


def test_dispatch_run_with_the_apps_first_input():
    out = dispatch(
        "run",
        {
            "context": {"read:portfolio_names": [{"id": "p", "name": "Personal"}]},
            "input": {"function": "main", "args": {}},
        },
    )
    assert out == portfolio_names_ui(["Personal"])


def test_dispatch_without_input_or_context_is_the_unavailable_state():
    assert dispatch("run", {}) == portfolio_names_ui(None)
    assert dispatch("run", None) == portfolio_names_ui(None)


@pytest.mark.parametrize(("function", "args"), [("other", {}), ("run", {"input": {"function": "delete_everything"}})])
def test_dispatch_rejects_unknown_functions(function, args):
    with pytest.raises(UnsupportedFunction):
        dispatch(function, args)
