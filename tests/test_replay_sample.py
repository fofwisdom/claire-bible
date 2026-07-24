"""replay_sample 토큰 입력과 로그 비식별화 회귀 테스트."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "replay_sample.py"
_SPEC = importlib.util.spec_from_file_location("replay_sample", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
replay_sample = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(replay_sample)


class ReplaySampleSecurityTest(unittest.TestCase):
    def test_loads_token_from_environment_by_default(self):
        token = replay_sample._load_token(
            None, None, {"CLAIRE_INJECT_TOKEN": "env-token"}
        )
        self.assertEqual(token, "env-token")

    def test_loads_token_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "token"
            token_file.write_text("file-token\n", encoding="utf-8")
            self.assertEqual(
                replay_sample._load_token(None, str(token_file), {}),
                "file-token",
            )

    def test_rejects_missing_or_multiline_token(self):
        with self.assertRaisesRegex(ValueError, "환경변수"):
            replay_sample._load_token(None, None, {})
        with self.assertRaisesRegex(ValueError, "공백이나 줄바꿈"):
            replay_sample._load_token("two tokens", None, {})

    def test_post_rejects_non_http_api_before_urlopen(self):
        with mock.patch.object(
            replay_sample.urllib.request,
            "urlopen",
            side_effect=AssertionError("urlopen must not be called"),
        ):
            with self.assertRaisesRegex(ValueError, r"http\(s\) URL"):
                replay_sample.post_ingest("file:///tmp/secret", "token", "payload")

    def test_log_entry_redacts_payload_url_and_bearer(self):
        line = replay_sample._log_entry(
            "12:34:56",
            1,
            report={
                "source_type": "web",
                "error": "GET https://private.example/doc failed; Bearer secret-token",
            },
        )
        encoded = json.dumps(line)
        self.assertEqual(line["payload"], "[redacted]")
        self.assertNotIn("private.example", encoded)
        self.assertNotIn("secret-token", encoded)
        self.assertIn("[redacted-url]", line["error"])

    def test_legacy_cli_token_still_works_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sample = root / "sample.md"
            log = root / "replay.jsonl"
            log.touch(mode=0o644)
            sample.write_text("", encoding="utf-8")
            stderr = io.StringIO()
            stdout = io.StringIO()
            argv = [
                "replay_sample.py",
                "--sample", str(sample),
                "--log", str(log),
                "--token", "legacy-token",
                "--interval", "0",
            ]
            with mock.patch.object(sys, "argv", argv):
                with (
                    contextlib.redirect_stderr(stderr),
                    contextlib.redirect_stdout(stdout),
                ):
                    self.assertEqual(replay_sample.main(), 0)
            self.assertIn("--token", stderr.getvalue())
            self.assertTrue(log.exists())
            self.assertEqual(log.read_text(encoding="utf-8"), "")
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
