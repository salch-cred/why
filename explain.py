"""Answering "why is this line here?" from the record."""

from __future__ import annotations

import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from . import lines as L
from .store import Store, key

QUERY = re.compile(r"^(?P<path>.+?)(?:[:#\s]+L?(?P<line>\d+))?\s*$")


def parse(query: str) -> tuple[str, Optional[int]]:
    """'app.py:42', 'app.py 42', 'app.py#L42' or just 'app.py'."""
    m = QUERY.match((query or "").strip().strip("`\"'"))
    if not m:
        return "", None
    return m.group("path").strip(), int(m.group("line")) if m.group("line") else None


def roots() -> list[Path]:
    seen, out = set(), []
    for raw in (os.environ.get("TERMINAL_CWD"), os.getcwd()):
        if raw and raw not in seen:
            seen.add(raw)
            out.append(Path(raw))
    return out


def locate(path: str, store: Store) -> tuple[Optional[Path], list[str]]:
    """The file the user means, and other recorded files that also match (when it is ambiguous)."""
    given = Path(os.path.expanduser(path))
    for candidate in ([given] if given.is_absolute() else [r / given for r in roots()]):
        if candidate.is_file():
            return candidate, []
    tail = os.path.normcase(os.path.normpath(path)).lstrip(os.sep + "/")
    matches = [p for p in store.known_paths()
               if p == key(path) or p.endswith(os.sep + tail) or p.endswith("/" + tail)]
    existing = [m for m in matches if Path(m).is_file()]
    if len(existing) == 1:
        return Path(existing[0]), []
    return None, existing or matches


def shown(path: str) -> str:
    for r in roots():
        try:
            return str(Path(path).relative_to(r))
        except ValueError:
            continue
    return str(path)


def when(ts: float) -> str:
    return time.strftime("%d %b %Y %H:%M", time.localtime(ts))


def quote(text: str, limit: int = 300) -> str:
    text = " ".join((text or "").split())
    if not text:
        return "(no request text was recorded)"
    return '"' + (text if len(text) <= limit else text[: limit - 1] + "…") + '"'


def origin(store: Store, path: Path, content: list[str], index: int) -> Optional[dict]:
    line = content[index]
    if not L.norm(line):
        return None
    prev, next_ = L.neighbours(content, index)
    edit_id = L.best(store.candidates(str(path), L.digest(line)), line, prev, next_)
    return store.edit(edit_id) if edit_id is not None else None


def read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def explain_line(store: Store, path: Path, number: int) -> str:
    content = read(path)
    if not 1 <= number <= len(content):
        return f"{shown(str(path))} has {len(content)} lines, so there is no line {number}."
    line = content[number - 1]
    head = f"{shown(str(path))}:{number}   {L.norm(line)[:120] or '(blank line)'}"
    if not L.norm(line):
        return head + "\nThis line is blank."
    edit = origin(store, path, content, number - 1)
    if edit is None:
        recorded = key(str(path)) in store.known_paths()
        return head + ("\nNot written by the agent while Why was recording, or changed by hand since."
                       if recorded else "\nWhy has no record of the agent writing to this file.")
    others = [shown(p) for p in store.files_in_turn(edit["session_id"], edit["turn_id"])
              if p != edit["path"]]
    out = [head,
           f"Written by the agent on {when(edit['ts'])} with {edit['tool']}"
           + (f" ({edit['model']})" if edit["model"] else "") + f", session {edit['session_id']}.",
           f"Because you asked: {quote(edit['request'])}"]
    if others:
        out.append("The same request also changed: " + ", ".join(sorted(others)[:8]))
    return "\n".join(out)


def explain_file(store: Store, path: Path) -> str:
    content = read(path)
    tally: Counter = Counter()
    edits: dict[int, dict] = {}
    total = 0
    for i, line in enumerate(content):
        if not L.norm(line):
            continue
        total += 1
        edit = origin(store, path, content, i)
        if edit:
            tally[edit["id"]] += 1
            edits[edit["id"]] = edit
    name = shown(str(path))
    if not tally:
        return f"{name}: none of its {total} lines match a recorded agent edit."
    out = [f"{name}: {sum(tally.values())} of {total} lines came from {len(tally)} "
           f"request{'s' if len(tally) != 1 else ''}:"]
    for edit_id, n in sorted(tally.items(), key=lambda kv: -edits[kv[0]]["ts"])[:10]:
        e = edits[edit_id]
        out.append(f"  {n:>4} lines  {when(e['ts'])}  {quote(e['request'], 120)}")
    out.append(f"{total - sum(tally.values())} lines were not written by the agent while Why was recording.")
    return "\n".join(out)


def explain_recent(store: Store, limit: int = 5) -> str:
    rows = store.recent(limit)
    if not rows:
        return ("Why hasn't recorded any agent edits yet. It starts with the next file the agent "
                "writes or patches. Ask: /why <file>:<line>")
    edits, lines_n, files = store.counts()
    out = [f"Why has recorded {lines_n} lines in {files} files from {edits} agent edits. Latest requests:"]
    for r in rows:
        out.append(f"  {when(r['ts'])}  {quote(r['request'], 100)}  -> "
                   + ", ".join(shown(p) for p in r["paths"][:4]))
    out.append("Ask about a line with /why <file>:<line>, or a whole file with /why <file>.")
    return "\n".join(out)


def answer(store: Store, query: str) -> str:
    query = (query or "").strip()
    if not query:
        return explain_recent(store)
    path, number = parse(query)
    found, options = locate(path, store)
    if found is None:
        if len(options) == 1 and not Path(options[0]).is_file():
            return f"Why has a record for {shown(options[0])}, but that file no longer exists there."
        if options:
            return "More than one recorded file matches. Be more specific:\n" + "\n".join(
                "  " + shown(o) for o in options[:10])
        return f"Can't find {path}. Give the path from the project folder, or an absolute path."
    return explain_line(store, found, number) if number else explain_file(store, found)
