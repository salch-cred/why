"""Parsing what the user asked about."""

import pytest
from why import explain


@pytest.mark.parametrize("query,expected", [
    ("app.py:42", ("app.py", 42)),
    ("src/app.py 7", ("src/app.py", 7)),
    ("app.py#L12", ("app.py", 12)),
    ("`app.py:3`", ("app.py", 3)),
    ("app.py", ("app.py", None)),
    (r"C:\work\app.py:9", (r"C:\work\app.py", 9)),
    (r"C:\work\app.py", (r"C:\work\app.py", None)),
])
def test_parse(query, expected):
    assert explain.parse(query) == expected


def test_quote_shortens_and_handles_empty():
    assert explain.quote("") == "(no request text was recorded)"
    long = explain.quote("x " * 400, 50)
    assert len(long) <= 52 and long.endswith('…"')
