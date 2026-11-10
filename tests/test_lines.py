"""Line fingerprints and choosing which edit wrote a line."""

import why.lines as L


def test_prints_skip_blank_lines_and_link_neighbours():
    ps = L.prints("a = 1\n\n  b = 2\nc = 3\n")
    assert [p.text for p in ps] == ["a = 1", "b = 2", "c = 3"]
    assert ps[1].prev == L.digest("a = 1") and ps[1].next == L.digest("c = 3")
    assert ps[0].prev == "" and ps[2].next == ""


def test_digest_ignores_surrounding_whitespace():
    assert L.digest("    x = 1  ") == L.digest("x = 1")


def test_neighbours_skip_blank_lines():
    content = ["a", "", "b", "", "", "c"]
    assert L.neighbours(content, 2) == (L.digest("a"), L.digest("c"))


def test_best_prefers_neighbour_agreement_over_recency():
    old = L.Print(L.digest("}"), L.digest("return a"), "", "}")
    new = L.Print(L.digest("}"), L.digest("return b"), "", "}")
    assert L.best([(1, old), (2, new)], "}", L.digest("return a"), "") == 1


def test_best_takes_latest_when_tied():
    p = L.Print(L.digest("total = price * quantity"), "", "", "total = price * quantity")
    assert L.best([(1, p), (2, p)], "total = price * quantity", "", "") == 2


def test_common_line_needs_a_neighbour():
    p = L.Print(L.digest("}"), "x", "y", "}")
    assert L.best([(1, p)], "}", "other", "other") is None


def test_no_candidates():
    assert L.best([], "x = 1", "", "") is None
