from __future__ import annotations

import unittest
from contextlib import suppress

from llm_tg_bot.providers import ClaudeAdapter, CodexAdapter, GeminiAdapter


class ProviderCommandTests(unittest.TestCase):
    def test_claude_uses_bypass_permissions_mode(self) -> None:
        request = ClaudeAdapter().prepare_request("hello", resume=False)

        self.assertEqual(
            request.command,
            (
                "claude",
                "-p",
                "--output-format",
                "text",
                "--permission-mode",
                "bypassPermissions",
                "hello",
            ),
        )

    def test_gemini_uses_yolo_approval_mode(self) -> None:
        request = GeminiAdapter().prepare_request("hello", resume=True)

        self.assertEqual(
            request.command,
            (
                "gemini",
                "--approval-mode",
                "yolo",
                "--resume",
                "latest",
                "-p",
                "hello",
                "--output-format",
                "text",
            ),
        )

    def test_codex_uses_full_access_and_never_asks_for_approval(self) -> None:
        request = CodexAdapter().prepare_request(
            "hello",
            resume=False,
            skip_git_repo_check=True,
        )

        try:
            self.assertEqual(
                request.command[:6],
                (
                    "codex",
                    "exec",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "--skip-git-repo-check",
                    "--color",
                    "never",
                ),
            )
            self.assertIsNotNone(request.output_file)
        finally:
            if request.output_file is not None:
                with suppress(FileNotFoundError):
                    request.output_file.unlink()
