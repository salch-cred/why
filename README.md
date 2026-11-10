# why

**Ask why any line of agent-written code exists, and get the request that caused it.**

Three weeks after an agent session you find `for attempt in range(3):` in your uploader. Who asked for three retries? Was it on purpose? `git blame` says only that you committed it, because the agent wrote it in your name.

Why answers that. Every time your Hermes agent writes or patches a file, Why records the lines it wrote and the request you made in that turn, on your machine. Then you ask:

```text
/why upload.py:4

upload.py:4   for attempt in range(3):
Written by the agent on 10 Nov 2026 19:16 with write_file (glm-5.3), session 20261110_191602_a1b2.
Because you asked: "add retries to the uploader, max 3, with backoff"
The same request also changed: tests/test_upload.py
```

## Use it

```text
/why upload.py:4       the request, date, model and session behind line 4
/why upload.py         which requests wrote which share of the file
/why                   the latest requests that changed files
hermes why upload.py:4    the same from your shell
```

The agent gets a `why_line` tool too, so if you ask "why is this retry loop here?", it looks the answer up instead of guessing.

```text
/why upload.py

upload.py: 8 of 9 lines came from 2 requests:
     1 lines  10 Nov 2026 19:16  "log each attempt"
     7 lines  10 Nov 2026 19:16  "add retries to the uploader, max 3, with backoff"
1 lines were not written by the agent while Why was recording.
```

## How it works

- **It records as the agent works.** A `pre_llm_call` hook stores the request of each turn. A `post_tool_call` hook stores the lines of each successful `write_file`, and the added lines of each successful `patch` (from the diff Hermes returns). Failed writes and other tools are ignored.
- **It matches lines by their text, not their number.** Lines you add above the agent's code don't break the link. A line you change by hand is no longer claimed: Why says it was "changed by hand since".
- **Short, common lines** such as `}` or `return` are matched by their neighbours too, so each one points to the request that wrote it.

## Limits

- **It only knows edits made while it was installed,** and only through `write_file` and `patch`. Files the agent changes with shell commands (`sed`, `echo >`) aren't recorded.
- **Identical lines:** if two requests wrote the very same lines in the same place, it names the most recent one.
- **Renamed files:** history is kept by path, so a file renamed after the edit loses its link.

## What it touches

- **Stores locally:** the text of each request (the first 600 characters, set by `excerpt_chars`) and the lines the agent wrote, in `<hermes home>/plugin-data/why/why.db`. Nothing leaves your machine.
- **Reads** the file you ask about, to find the line.
- **No model calls, no network, no telemetry, no dependencies.**

To forget everything, delete `why.db`.

## Settings

```yaml
plugins:
  entries:
    why:
      settings:
        excerpt_chars: 600     # how much of each request to keep
```

## Development

```bash
pytest tests
```

34 tests cover line matching after moves and hand edits, common lines, write and patch records, failed tools, path lookup, the `/why` views, the tool and the CLI.

## License

MIT
