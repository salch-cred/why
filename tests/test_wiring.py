"""The plugin as Hermes drives it: requests, writes and patches in, answers out."""

import argparse
import json
import os

import pytest
import why


class FakeCtx:
    def __init__(self, settings=None):
        self.settings, self.hooks, self.commands, self.tools, self.cli = settings or {}, {}, {}, {}, {}

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, cb, **_):
        self.hooks.setdefault(name, []).append(cb)

    def register_command(self, name, handler, **_):
        self.commands[name] = handler

    def register_tool(self, name, toolset, schema, handler, **_):
        self.tools[name] = (schema, handler)

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, **_):
        self.cli[name] = (setup_fn, handler_fn)

    def fire(self, name, **kw):
        return [cb(**kw) for cb in self.hooks.get(name, [])]


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    fake = FakeCtx()
    why.register(fake)
    return fake


def ask(ctx, text, turn="T1", session="S1"):
    ctx.fire("pre_llm_call", session_id=session, turn_id=turn, user_message=text, model="glm-5.3",
             platform="cli")


def write(ctx, path, content, turn="T1", session="S1", status="ok"):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    ctx.fire("post_tool_call", tool_name="write_file", args={"path": path, "content": content},
             result=json.dumps({"bytes_written": len(content)}), status=status, session_id=session,
             turn_id=turn)


def patch(ctx, path, new_string, result, turn="T1", session="S1"):
    ctx.fire("post_tool_call", tool_name="patch",
             args={"path": path, "old_string": "x", "new_string": new_string},
             result=json.dumps(result), status="ok", session_id=session, turn_id=turn)


def rewrite(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def why_(ctx, q):
    return ctx.commands["why"](q)


UPLOADER = ("import time\n\ndef upload(f):\n    for attempt in range(3):\n        try:\n"
            "            return send(f)\n        except IOError:\n            time.sleep(2 ** attempt)\n")


def test_registers_everything(ctx):
    assert set(ctx.hooks) == {"pre_llm_call", "post_tool_call"}
    assert "why" in ctx.commands and "why_line" in ctx.tools and "why" in ctx.cli


def test_line_points_to_the_request(ctx):
    ask(ctx, "add retries to the uploader, max 3, with backoff")
    write(ctx, "upload.py", UPLOADER)
    out = why_(ctx, "upload.py:4")
    assert "for attempt in range(3):" in out
    assert '"add retries to the uploader, max 3, with backoff"' in out
    assert "write_file" in out and "glm-5.3" in out and "session S1" in out


def test_line_is_found_after_it_moves(ctx):
    ask(ctx, "add retries")
    write(ctx, "upload.py", UPLOADER)
    rewrite("upload.py", "# my header\n# more\n\n" + UPLOADER)      # the user adds lines on top
    assert "add retries" in why_(ctx, "upload.py:7")


def test_hand_edited_line_is_not_claimed(ctx):
    ask(ctx, "add retries")
    write(ctx, "upload.py", UPLOADER)
    rewrite("upload.py", UPLOADER.replace("range(3)", "range(5)"))
    assert "Not written by the agent" in why_(ctx, "upload.py:4")


def test_two_requests_each_get_their_own_lines(ctx):
    ask(ctx, "create the uploader", turn="T1")
    write(ctx, "upload.py", "def upload(f):\n    return send(f)\n", turn="T1")
    ask(ctx, "log every upload", turn="T2")
    patch(ctx, "upload.py", "    log.info('upload %s', f)\n    return send(f)",
          {"success": True, "diff": "--- a\n+++ b\n@@\n def upload(f):\n+    log.info('upload %s', f)\n     return send(f)\n"},
          turn="T2")
    rewrite("upload.py", "def upload(f):\n    log.info('upload %s', f)\n    return send(f)\n")
    assert "log every upload" in why_(ctx, "upload.py:2")
    assert "create the uploader" in why_(ctx, "upload.py:3")      # unchanged context is not re-claimed


def test_patch_without_diff_uses_new_string(ctx):
    ask(ctx, "rename the timeout")
    rewrite("cfg.py", "REQUEST_TIMEOUT_SECONDS = 30\n")
    patch(ctx, "cfg.py", "REQUEST_TIMEOUT_SECONDS = 30", {"success": True})
    assert "rename the timeout" in why_(ctx, "cfg.py:1")


def test_failed_writes_and_other_tools_are_ignored(ctx):
    ask(ctx, "try something")
    write(ctx, "a.py", "value_from_failed_write = 1\n", status="error")
    ctx.fire("post_tool_call", tool_name="terminal", args={"command": "echo hi > a.py"}, result="{}",
             status="ok", session_id="S1", turn_id="T1")
    patch(ctx, "a.py", "value_from_failed_write = 1", {"success": False, "error": "no match"})
    assert "no record" in why_(ctx, "a.py:1")


def test_common_line_is_disambiguated_by_neighbours(ctx):
    ask(ctx, "first function", turn="T1")
    write(ctx, "m.js", "function a() {\n  return 1;\n}\n", turn="T1")
    ask(ctx, "second function", turn="T2")
    write(ctx, "m.js", "function b() {\n  return 2;\n}\n", turn="T2")
    rewrite("m.js", "function a() {\n  return 1;\n}\n")
    assert "first function" in why_(ctx, "m.js:3")


def test_file_summary(ctx):
    ask(ctx, "create the uploader")
    write(ctx, "upload.py", UPLOADER)
    with open("upload.py", "a", encoding="utf-8") as f:
        f.write("# TODO by me\n")
    out = why_(ctx, "upload.py")
    assert "7 of 8 lines came from 1 request" in out and "1 lines were not written" in out


def test_recent_and_empty(ctx):
    assert "hasn't recorded" in why_(ctx, "")
    ask(ctx, "create the uploader")
    write(ctx, "upload.py", UPLOADER)
    out = why_(ctx, "")
    assert "create the uploader" in out and "upload.py" in out


def test_found_by_recorded_path_when_cwd_differs(ctx, tmp_path, monkeypatch):
    os.makedirs(tmp_path / "proj" / "src")
    ask(ctx, "make the parser")
    write(ctx, str(tmp_path / "proj" / "src" / "parse.py"), "def parse(text):\n    return text.split(',')\n")
    monkeypatch.chdir(tmp_path)                                     # src/parse.py isn't under cwd
    assert "make the parser" in why_(ctx, "src/parse.py:2")


def test_relative_write_is_resolved_against_terminal_cwd(ctx, tmp_path, monkeypatch):
    os.makedirs(tmp_path / "work")
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path / "work"))
    ask(ctx, "make the parser")
    rewrite(str(tmp_path / "work" / "p.py"), "def parse(text):\n    return text.split(',')\n")
    ctx.fire("post_tool_call", tool_name="write_file",
             args={"path": "p.py", "content": "def parse(text):\n    return text.split(',')\n"},
             result="{}", status="ok", session_id="S1", turn_id="T1")
    assert "make the parser" in why_(ctx, "p.py:1")


def test_ambiguous_and_missing(ctx, tmp_path):
    os.makedirs(tmp_path / "a")
    os.makedirs(tmp_path / "b")
    ask(ctx, "x")
    write(ctx, str(tmp_path / "a" / "util.py"), "alpha_value = 1\n")
    write(ctx, str(tmp_path / "b" / "util.py"), "beta_value = 2\n")
    assert "More than one" in why_(ctx, "util.py:1")
    assert "Can't find" in why_(ctx, "nothing.py:1")


def test_line_out_of_range_and_blank(ctx):
    ask(ctx, "x")
    write(ctx, "u.py", "a_long_enough_line = 1\n\nanother_line = 2\n")
    assert "no line 9" in why_(ctx, "u.py:9")
    assert "blank" in why_(ctx, "u.py:2")


def test_multimodal_message_and_excerpt_limit(monkeypatch):
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    fake = FakeCtx({"excerpt_chars": 20})
    why.register(fake)
    ask(fake, [{"type": "text", "text": "build the screenshot cropper please, carefully"},
               {"type": "image_url", "image_url": {"url": "data:"}}])
    write(fake, "crop.py", "def crop(image, box):\n    return image.crop(box)\n")
    out = why_(fake, "crop.py:2")
    assert '"build the screenshot' in out and "carefully" not in out


def test_tool_answers_as_json(ctx):
    ask(ctx, "add retries")
    write(ctx, "upload.py", UPLOADER)
    schema, handler = ctx.tools["why_line"]
    assert schema["parameters"]["required"] == ["path"]
    assert "add retries" in json.loads(handler({"path": "upload.py", "line": 4}))["answer"]
    assert "error" in json.loads(handler({}))


def test_cli(ctx, capsys):
    ask(ctx, "add retries")
    write(ctx, "upload.py", UPLOADER)
    setup, run = ctx.cli["why"]
    parser = argparse.ArgumentParser()
    setup(parser)
    assert run(parser.parse_args(["upload.py:4"])) == 0
    assert "add retries" in capsys.readouterr().out


def test_hooks_never_raise_on_junk(ctx):
    assert ctx.fire("post_tool_call", tool_name="write_file", args=None, result=None) == [None]
    assert ctx.fire("post_tool_call", tool_name="write_file", args={"path": 5, "content": "x"}) == [None]
    assert ctx.fire("pre_llm_call", user_message=None) == [None]


def test_recorded_file_that_was_deleted(ctx, tmp_path):
    os.makedirs(tmp_path / "lib")
    ask(ctx, "x")
    write(ctx, str(tmp_path / "lib" / "gone.py"), "soon_deleted = 1\n")
    os.remove(tmp_path / "lib" / "gone.py")
    assert "no longer exists" in why_(ctx, "gone.py:1")
