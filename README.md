# attentionspan

`attentionspan` is a tiny Claude Code wrapper that makes the assistant increasingly terse and impatient as the context window fills.

It works with two local hooks:

- `statusline` reads Claude Code's status-line JSON, shows the current context pressure, and stores the latest session snapshot on disk.
- `hook` runs on `UserPromptSubmit`, reloads that snapshot, and injects stronger response-style instructions once context usage crosses your thresholds.

No dependencies are required beyond Python 3.

## Default behavior

By default, the mode ramps like this:

- `< 60%`: normal
- `60% - 79.9%`: focused
- `80% - 91.9%`: impatient
- `>= 92%`: critical

The injected instructions get progressively stricter:

- `focused`: concise, direct, minimal background
- `impatient`: brisk tone, no filler, no recap, smallest complete answer
- `critical`: visibly impatient, heavily compressed, single next step for broad tasks

You can override the thresholds with `ATTENTIONSPAN_THRESHOLDS`, for example:

```bash
export ATTENTIONSPAN_THRESHOLDS="55,75,90"
```

## Install

Clone the repo somewhere stable, then make the script executable:

```bash
chmod +x /absolute/path/to/attentionspan/attentionspan.py
```

Point Claude Code at the script from `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 /absolute/path/to/attentionspan/attentionspan.py statusline",
    "padding": 0
  },
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/attentionspan/attentionspan.py hook"
          }
        ]
      }
    ]
  }
}
```

Claude Code writes the latest session snapshots to `~/.claude/attentionspan/state/` by default. To move that elsewhere, set:

```bash
export ATTENTIONSPAN_HOME="/some/other/path"
```

## How it works

`UserPromptSubmit` hooks do not receive `context_window.used_percentage`, so the hook cannot know current context pressure on its own.

This repo works around that by using the status line as the state producer:

1. Claude Code runs the `statusline` command after assistant responses.
2. `attentionspan.py statusline` stores the latest `used_percentage`, token counts, and mode for that `session_id`.
3. On the next user prompt, Claude Code runs `attentionspan.py hook`.
4. The hook reloads the saved state for that `session_id` and emits `additionalContext` only when the session has crossed a threshold.

This keeps everything local, cheap, and easy to tune.

## Verify locally

Run the test suite:

```bash
python3 -m unittest discover -s tests
```

You can also smoke-test each entrypoint by piping JSON into it:

```bash
ATTENTIONSPAN_HOME="$(pwd)/.attentionspan-test" python3 attentionspan.py statusline <<'EOF'
{"session_id":"abc123","model":{"display_name":"Sonnet"},"workspace":{"current_dir":"/tmp/demo","project_dir":"/tmp/demo"},"cost":{"total_cost_usd":0.42,"total_duration_ms":120000},"context_window":{"context_window_size":200000,"used_percentage":84,"current_usage":{"input_tokens":120000,"cache_creation_input_tokens":20000,"cache_read_input_tokens":8000,"output_tokens":4000}}}
EOF
```

```bash
ATTENTIONSPAN_HOME="$(pwd)/.attentionspan-test" python3 attentionspan.py hook <<'EOF'
{"session_id":"abc123","hook_event_name":"UserPromptSubmit","prompt":"continue"}
EOF
```
