import os
import tempfile
import unittest

import attentionspan


SAMPLE_STATUS_PAYLOAD = {
    "session_id": "abc123",
    "cwd": "/tmp/demo",
    "transcript_path": "/tmp/demo/.claude/session.jsonl",
    "model": {"display_name": "Sonnet"},
    "workspace": {
        "current_dir": "/tmp/demo",
        "project_dir": "/tmp/demo",
        "added_dirs": [],
    },
    "cost": {
        "total_cost_usd": 0.42,
        "total_duration_ms": 120000,
    },
    "context_window": {
        "context_window_size": 200000,
        "used_percentage": 84,
        "current_usage": {
            "input_tokens": 120000,
            "cache_creation_input_tokens": 20000,
            "cache_read_input_tokens": 8000,
            "output_tokens": 4000,
        },
    },
}


class AttentionSpanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_home = os.environ.get("ATTENTIONSPAN_HOME")
        os.environ["ATTENTIONSPAN_HOME"] = self.tempdir.name

    def tearDown(self) -> None:
        if self.original_home is None:
            os.environ.pop("ATTENTIONSPAN_HOME", None)
        else:
            os.environ["ATTENTIONSPAN_HOME"] = self.original_home
        self.tempdir.cleanup()

    def test_mode_thresholds(self) -> None:
        self.assertEqual(attentionspan.mode_for_percentage(30).name, "normal")
        self.assertEqual(attentionspan.mode_for_percentage(60).name, "focused")
        self.assertEqual(attentionspan.mode_for_percentage(80).name, "impatient")
        self.assertEqual(attentionspan.mode_for_percentage(92).name, "critical")

    def test_status_state_persists_latest_snapshot(self) -> None:
        state = attentionspan.status_state(SAMPLE_STATUS_PAYLOAD)
        attentionspan.persist_state(state)

        loaded = attentionspan.load_state_for_session("abc123")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["mode"], "impatient")
        self.assertEqual(loaded["input_side_tokens"], 148000)
        self.assertEqual(loaded["context_window_size"], 200000)

    def test_hook_response_includes_additional_context(self) -> None:
        state = attentionspan.status_state(SAMPLE_STATUS_PAYLOAD)
        attentionspan.persist_state(state)

        response = attentionspan.build_hook_response(
            {
                "session_id": "abc123",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "continue",
            }
        )

        self.assertIsNotNone(response)
        hook_output = response["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "UserPromptSubmit")
        self.assertIn("AttentionSpan mode: impatient.", hook_output["additionalContext"])
        self.assertIn("deliberately impatient", hook_output["additionalContext"])

    def test_normal_mode_produces_no_hook_output(self) -> None:
        payload = dict(SAMPLE_STATUS_PAYLOAD)
        payload["context_window"] = dict(SAMPLE_STATUS_PAYLOAD["context_window"])
        payload["context_window"]["used_percentage"] = 20
        payload["context_window"]["current_usage"] = {
            "input_tokens": 30000,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 1000,
        }
        state = attentionspan.status_state(payload)
        attentionspan.persist_state(state)

        response = attentionspan.build_hook_response({"session_id": "abc123"})
        self.assertIsNone(response)


if __name__ == "__main__":
    unittest.main()
