"""Line fingerprints: how Why recognises a line it saw the agent write, even after it has moved.

A line is matched by its text (whitespace at the ends ignored), not by its number, so lines
added above it don't break the link. Short, common lines such as "}" or "return" also carry the
fingerprints of the lines around them, so the best match is the one whose neighbours agree too.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

COMMON = 12          # a line this short or shorter is too common to trust on its own


def norm(line: str) -> str:
    return line.strip()


def digest(line: str) -> str:
    return hashlib.sha1(norm(line).encode("utf-8", "replace")).hexdigest()[:16]


@dataclass(frozen=True)
class Print:
    """One written line: its fingerprint and those of the nearest non-blank lines around it."""
    hash: str
    prev: str
    next: str
    text: str


def prints(text: str) -> list[Print]:
    """Fingerprints for every non-blank line of *text*, in order."""
    kept = [line for line in (text or "").splitlines() if norm(line)]
    out = []
    for i, line in enumerate(kept):
        out.append(Print(hash=digest(line),
                         prev=digest(kept[i - 1]) if i > 0 else "",
                         next=digest(kept[i + 1]) if i + 1 < len(kept) else "",
                         text=norm(line)[:200]))
    return out


def neighbours(lines: list[str], index: int) -> tuple[str, str]:
    """Fingerprints of the nearest non-blank lines above and below lines[index] in a file."""
    prev = next_ = ""
    for j in range(index - 1, -1, -1):
        if norm(lines[j]):
            prev = digest(lines[j])
            break
    for j in range(index + 1, len(lines)):
        if norm(lines[j]):
            next_ = digest(lines[j])
            break
    return prev, next_


def score(candidate: Print, prev: str, next_: str) -> int:
    """How well a stored line fits the line in the file now: 0 to 2 neighbours agree."""
    return int(bool(prev) and candidate.prev == prev) + int(bool(next_) and candidate.next == next_)


def is_common(line: str) -> bool:
    return len(norm(line)) <= COMMON


def best(candidates: list[tuple[int, Print]], line: str, prev: str, next_: str) -> Optional[int]:
    """Pick the edit id that most likely wrote *line*. Candidates are (edit_id, Print), oldest
    first. Neighbour agreement wins, then the most recent edit. A common line needs at least one
    neighbour to agree, or nothing is claimed."""
    if not candidates:
        return None
    ranked = sorted(((score(p, prev, next_), i, edit_id) for i, (edit_id, p) in enumerate(candidates)),
                    reverse=True)
    top_score, _, edit_id = ranked[0]
    if is_common(line) and top_score == 0:
        return None
    return edit_id
