"""why: ask why any line of agent-written code exists, and get the request that caused it.

Every time the agent writes or patches a file, Why records the lines it wrote and the user
request of that turn, locally. Later:

  /why app.py:42      the request, date, model and session that wrote line 42
  /why app.py         how many of the file's lines came from which requests
  /why                the latest requests that changed files
  hermes why app.py:42   the same from your shell

The agent gets a `why_line` tool, so "why is this retry loop here?" gets a real answer.
Lines are matched by their text, not their number, so they are still found after they move.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from . import explain
from . import lines as L
from .store import Store

if TYPE_CHECKING:
    from hermes_cli.plugins import PluginContext

_CTX: Any = None
_STORE: Optional[Store] = None
_STORE_HOME: Optional[Path] = None
WRITE_TOOLS = {"write_file", "patch"}
_LOCK = threading.Lock()
_TURNS: dict[tuple[str, str], tuple[str, str, str]] = {}   # requests held until their turn writes a file
_LATEST: dict[str, str] = {}


def _setting(name: str, default: Any) -> Any:
    try:
        value = _CTX.get_config(name, default) if _CTX is not None else default
    except Exception:
        value = default
    return default if value is None else value


def _data_dir() -> Path:
    try:
        from plugins.plugin_storage import plugin_data_dir
        return plugin_data_dir("why")
    except Exception:
        from hermes_constants import get_hermes_home
        path = get_hermes_home() / "plugin-data" / "why"
        path.mkdir(parents=True, exist_ok=True)
        return path


def store() -> Store:
    """One store per Hermes home, so a profile switch gets its own record."""
    global _STORE, _STORE_HOME
    home = _data_dir()
    if _STORE is None or _STORE_HOME != home:
        _STORE, _STORE_HOME = Store(home / "why.db"), home
    return _STORE


def _text(message: Any) -> str:
    """The text of a user message, also when it is a list of content parts."""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return " ".join(p.get("text", "") for p in message if isinstance(p, dict) and p.get("type") == "text")
    if isinstance(message, dict):
        return _text(message.get("content"))
    return ""


def _parsed(result: Any) -> dict:
    if isinstance(result, dict):
        return result
    try:
        value = json.loads(result) if isinstance(result, str) else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _where(raw: str, result: dict) -> str:
    """The absolute path the tool wrote to: the tool's own answer, else the file that exists."""
    for name in ("files_modified", "files_created"):
        listed = result.get(name)
        if isinstance(listed, list) and listed and isinstance(listed[0], str):
            if os.path.isabs(listed[0]):
                return listed[0]
    for name in ("resolved_path", "path"):
        value = result.get(name)
        if isinstance(value, str) and os.path.isabs(value):
            return value
    given = os.path.expanduser(raw)
    if os.path.isabs(given):
        return given
    tries = [os.path.join(r, given) for r in (os.environ.get("TERMINAL_CWD"), os.getcwd()) if r]
    return next((t for t in tries if os.path.isfile(t)), tries[-1])


def _added(diff: str) -> str:
    return "\n".join(line[1:] for line in diff.splitlines()
                     if line.startswith("+") and not line.startswith("+++"))


# hooks ------------------------------------------------------------------

def on_pre_llm_call(session_id: str = "", turn_id: str = "", user_message: Any = None,
                    model: str = "", platform: str = "", **_: Any) -> None:
    """Hold this turn's request in memory. It is written to disk only if the turn writes a file,
    so why.db never becomes a log of every message."""
    text = _text(user_message).strip()
    if not text:
        return None
    limit = int(_setting("excerpt_chars", 600) or 600)
    with _LOCK:
        _TURNS[(session_id or "", turn_id or "")] = (text[:limit], model or "", platform or "")
        _LATEST[session_id or ""] = turn_id or ""
        while len(_TURNS) > 200:
            _TURNS.pop(next(iter(_TURNS)))
    return None


def _keep_request(session_id: str, turn_id: str) -> None:
    """Save the request behind this turn, now that it has written a file."""
    with _LOCK:
        key_ = (session_id, turn_id)
        if key_ not in _TURNS:
            key_ = (session_id, _LATEST.get(session_id, ""))
        held = _TURNS.pop(key_, None)
    if held:
        store().save_request(key_[0], key_[1], *held)


def on_post_tool_call(tool_name: str = "", args: Any = None, result: Any = None, status: str = "",
                      session_id: str = "", turn_id: str = "", **_: Any) -> None:
    if tool_name not in WRITE_TOOLS or not isinstance(args, dict) or status == "error":
        return None
    parsed = _parsed(result)
    if parsed.get("error") or parsed.get("success") is False:
        return None
    raw = args.get("path") or ""
    if not isinstance(raw, str) or not raw:
        return None
    if tool_name == "write_file":
        written = args.get("content") or ""
    else:
        diff = parsed.get("diff")
        written = _added(diff) if isinstance(diff, str) and diff.strip() else (args.get("new_string") or "")
    if not isinstance(written, str) or not written.strip():
        return None
    try:
        _keep_request(session_id or "", turn_id or "")
        store().save_edit(session_id=session_id or "", turn_id=turn_id or "", path=_where(raw, parsed),
                          tool=tool_name, written=L.prints(written))
    except Exception:
        pass
    return None


# command, tool, CLI -----------------------------------------------------

def why_command(raw_args: str = "", **_: Any) -> str:
    try:
        return explain.answer(store(), raw_args)
    except Exception as exc:
        return f"Why couldn't answer: {exc}"


WHY_LINE_SCHEMA = {
    "name": "why_line",
    "description": (
        "Find out why a line or file of code exists: returns the user's request that made the agent "
        "write it, with the date, model and session. Use it when the user asks why some code is "
        "there, who asked for it, or what it was for, before guessing. Works for lines the agent "
        "wrote with write_file or patch while the Why plugin was recording."),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path, relative to the project or absolute."},
            "line": {"type": "integer", "description": "1-based line number. Omit for a whole-file summary."},
        },
        "required": ["path"],
    },
}


def why_line(args: Optional[dict] = None, **_: Any) -> str:
    args = args or {}
    path = str(args.get("path") or "").strip()
    if not path:
        return json.dumps({"error": "path is required"})
    line = args.get("line")
    query = f"{path}:{int(line)}" if isinstance(line, (int, float)) and line else path
    try:
        return json.dumps({"answer": explain.answer(store(), query)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _cli_setup(parser: Any) -> None:
    parser.add_argument("target", nargs="?", default="", help="file:line, a file, or nothing for recent requests")


def _cli_run(args: Any) -> int:
    print(why_command(getattr(args, "target", "") or ""))
    return 0


def register(ctx: "PluginContext") -> None:
    global _CTX
    _CTX = ctx
    with _LOCK:
        _TURNS.clear()
        _LATEST.clear()
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_command("why", why_command,
                         description="Why is this line here? Shows the request that made the agent write it.",
                         args_hint="<file>:<line>")
    ctx.register_tool(name="why_line", toolset="why", schema=WHY_LINE_SCHEMA, handler=why_line,
                      description=WHY_LINE_SCHEMA["description"])
    try:
        ctx.register_cli_command("why", "Show the request that made the agent write a line of code",
                                 _cli_setup, _cli_run)
    except Exception:
        pass
