#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_THRESHOLDS = (60.0, 80.0, 92.0)
BAR_WIDTH = 10
RESET = "\033[0m"
COLORS = {
    "normal": "\033[32m",
    "focused": "\033[33m",
    "impatient": "\033[91m",
    "critical": "\033[31m",
}


@dataclass(frozen=True)
class Mode:
    name: str
    description: str
    instructions: tuple[str, ...]


MODES = {
    "normal": Mode(
        name="normal",
        description="Normal tone",
        instructions=(),
    ),
    "focused": Mode(
        name="focused",
        description="Concise mode",
        instructions=(
            "Context usage is rising. Keep the reply concise and directly useful.",
            "Skip restating the user's request or giving background unless it changes the answer.",
            "Prefer one short paragraph or a few flat bullets.",
        ),
    ),
    "impatient": Mode(
        name="impatient",
        description="Brisk mode",
        instructions=(
            "Context is tight. Respond with a deliberately impatient, brisk tone.",
            "Answer directly. Skip pleasantries, recaps, alternatives, and optional extras.",
            "Prefer the smallest complete answer or the next concrete action.",
            "Ask at most one blocking question if you are truly blocked.",
        ),
    ),
    "critical": Mode(
        name="critical",
        description="Critical compression mode",
        instructions=(
            "Context is nearly exhausted. Be visibly impatient and highly compressed.",
            "Use the fewest words that still solve the task.",
            "No recap, no motivational language, and no caveats unless safety-critical.",
            "If the task is broad, give only the single highest-value next step.",
        ),
    ),
}


def attentionspan_home() -> Path:
    configured = os.environ.get("ATTENTIONSPAN_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude" / "attentionspan"


def state_dir() -> Path:
    return attentionspan_home() / "state"


def thresholds() -> tuple[float, float, float]:
    raw = os.environ.get("ATTENTIONSPAN_THRESHOLDS")
    if not raw:
        return DEFAULT_THRESHOLDS

    values = [part.strip() for part in raw.split(",") if part.strip()]
    if len(values) != 3:
        raise ValueError(
            "ATTENTIONSPAN_THRESHOLDS must contain exactly three comma-separated numbers"
        )
    parsed = tuple(float(value) for value in values)
    if parsed != tuple(sorted(parsed)):
        raise ValueError("ATTENTIONSPAN_THRESHOLDS must be sorted in ascending order")
    return parsed


def mode_for_percentage(used_percentage: float) -> Mode:
    focused_at, impatient_at, critical_at = thresholds()
    if used_percentage >= critical_at:
        return MODES["critical"]
    if used_percentage >= impatient_at:
        return MODES["impatient"]
    if used_percentage >= focused_at:
        return MODES["focused"]
    return MODES["normal"]


def read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    return json.loads(raw)


def slug(value: str) -> str:
    return "".join(character for character in value if character.isalnum() or character in "-_.")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def usage_input_tokens(current_usage: dict[str, Any] | None) -> int:
    if not current_usage:
        return 0
    return int(current_usage.get("input_tokens", 0)) + int(
        current_usage.get("cache_creation_input_tokens", 0)
    ) + int(current_usage.get("cache_read_input_tokens", 0))


def normalize_percentage(payload: dict[str, Any]) -> float:
    context_window = payload.get("context_window", {})
    used_percentage = context_window.get("used_percentage")
    if used_percentage is not None:
        return float(used_percentage)

    context_window_size = int(context_window.get("context_window_size", 0) or 0)
    if context_window_size <= 0:
        return 0.0

    current_usage = context_window.get("current_usage")
    input_side_tokens = usage_input_tokens(current_usage)
    if input_side_tokens <= 0:
        return 0.0

    return round((input_side_tokens / context_window_size) * 100, 1)


def status_state(payload: dict[str, Any]) -> dict[str, Any]:
    context_window = payload.get("context_window", {})
    current_usage = context_window.get("current_usage")
    used_percentage = normalize_percentage(payload)
    input_side_tokens = usage_input_tokens(current_usage)
    context_window_size = int(context_window.get("context_window_size", 0) or 0)
    session_id = payload.get("session_id") or "unknown-session"
    mode = mode_for_percentage(used_percentage)

    return {
        "session_id": session_id,
        "updated_at": int(time.time()),
        "mode": mode.name,
        "used_percentage": used_percentage,
        "context_window_size": context_window_size,
        "input_side_tokens": input_side_tokens,
        "output_tokens": int((current_usage or {}).get("output_tokens", 0)),
        "cost_total_usd": float(payload.get("cost", {}).get("total_cost_usd", 0.0) or 0.0),
        "project_dir": payload.get("workspace", {}).get("project_dir") or payload.get("cwd"),
        "transcript_path": payload.get("transcript_path"),
        "model_display_name": payload.get("model", {}).get("display_name") or "Unknown",
    }


def state_path(session_id: str) -> Path:
    return state_dir() / f"{slug(session_id)}.json"


def persist_state(state: dict[str, Any]) -> None:
    atomic_write_json(state_path(state["session_id"]), state)


def load_state_for_session(session_id: str) -> dict[str, Any] | None:
    path = state_path(session_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def prune_state_files(max_age_seconds: int = 60 * 60 * 24 * 7) -> None:
    now = time.time()
    directory = state_dir()
    if not directory.exists():
        return

    for candidate in directory.glob("*.json"):
        try:
            age = now - candidate.stat().st_mtime
            if age > max_age_seconds:
                candidate.unlink()
        except OSError:
            continue


def progress_bar(used_percentage: float) -> str:
    clamped = max(0.0, min(100.0, used_percentage))
    filled = int(round((clamped / 100.0) * BAR_WIDTH))
    filled = max(0, min(BAR_WIDTH, filled))
    return ("█" * filled) + ("░" * (BAR_WIDTH - filled))


def format_duration(duration_ms: float) -> str:
    total_seconds = int(duration_ms // 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {seconds}s"


def render_statusline(payload: dict[str, Any], state: dict[str, Any]) -> str:
    model_name = state["model_display_name"]
    project_dir = payload.get("workspace", {}).get("current_dir") or payload.get("cwd") or ""
    project_name = Path(project_dir).name if project_dir else "unknown"
    used_percentage = state["used_percentage"]
    cost_usd = state["cost_total_usd"]
    duration_ms = float(payload.get("cost", {}).get("total_duration_ms", 0) or 0)
    mode = MODES[state["mode"]]
    bar = progress_bar(used_percentage)
    color = COLORS[mode.name]
    size = state["context_window_size"]
    input_tokens = state["input_side_tokens"]

    return (
        f"[{model_name}] {project_name} "
        f"{color}{bar}{RESET} "
        f"{used_percentage:>5.1f}% | {mode.name} | "
        f"{input_tokens:,}/{size:,} | ${cost_usd:.2f} | {format_duration(duration_ms)}"
    )


def build_additional_context(state: dict[str, Any]) -> str | None:
    mode = MODES[state["mode"]]
    if mode.name == "normal":
        return None

    used_percentage = round(float(state["used_percentage"]), 1)
    size = int(state["context_window_size"])
    input_side_tokens = int(state["input_side_tokens"])

    lines = [
        (
            f"AttentionSpan mode: {mode.name}. "
            f"Latest context usage is about {used_percentage}% "
            f"({input_side_tokens:,}/{size:,} input-side tokens)."
        )
    ]
    lines.extend(mode.instructions)
    return "\n".join(lines)


def build_hook_response(payload: dict[str, Any]) -> dict[str, Any] | None:
    session_id = payload.get("session_id")
    if not session_id:
        return None

    state = load_state_for_session(session_id)
    if not state:
        return None

    additional_context = build_additional_context(state)
    if not additional_context:
        return None

    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        }
    }


def command_statusline(_: argparse.Namespace) -> int:
    payload = read_stdin_json()
    state = status_state(payload)
    persist_state(state)
    prune_state_files()
    print(render_statusline(payload, state))
    return 0


def command_hook(_: argparse.Namespace) -> int:
    payload = read_stdin_json()
    response = build_hook_response(payload)
    if response:
        print(json.dumps(response))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Make Claude Code terser and more impatient as the context window fills."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    statusline_parser = subparsers.add_parser(
        "statusline",
        help="Render a status line and persist the latest context usage snapshot.",
    )
    statusline_parser.set_defaults(func=command_statusline)

    hook_parser = subparsers.add_parser(
        "hook",
        help="Inject stricter response-style instructions based on the latest session state.",
    )
    hook_parser.set_defaults(func=command_hook)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except json.JSONDecodeError as exc:
        if len(sys.argv) > 1 and sys.argv[1] == "statusline":
            print(f"[attentionspan] invalid JSON: {exc.msg}")
            raise SystemExit(0)
        raise SystemExit(0)
    except Exception as exc:  # noqa: BLE001
        if len(sys.argv) > 1 and sys.argv[1] == "statusline":
            print(f"[attentionspan] {exc}")
        raise SystemExit(0)
