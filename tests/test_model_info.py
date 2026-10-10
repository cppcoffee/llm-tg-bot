from __future__ import annotations

import unittest

from llm_tg_bot.model_info import _model_info_from_response


class ModelInfoParseTests(unittest.TestCase):
    def test_get_state_response_extracts_model_and_thinking(self) -> None:
        record = {
            "type": "response",
            "command": "get_state",
            "success": True,
            "data": {
                "model": {
                    "id": "deepseek-v4.1-flash",
                    "name": "DeepSeek V4.1 Flash",
                    "provider": "quickdesk-ai-cc",
                },
                "thinkingLevel": "high",
            },
        }

        info = _model_info_from_response(record)

        assert info is not None
        self.assertEqual(info.model, "DeepSeek V4.1 Flash (quickdesk-ai-cc)")
        self.assertEqual(info.thinking_level, "high")
        self.assertEqual(
            info.describe(),
            "Model: DeepSeek V4.1 Flash (quickdesk-ai-cc)\nThinking: high",
        )

    def test_missing_model_returns_none(self) -> None:
        record = {"type": "response", "command": "get_state", "data": {}}
        self.assertIsNone(_model_info_from_response(record))

    def test_model_without_name_or_provider_still_describes(self) -> None:
        info = _model_info_from_response(
            {"data": {"model": {"id": "gpt-5"}, "thinkingLevel": None}}
        )

        assert info is not None
        self.assertEqual(info.model, "gpt-5")
        self.assertIsNone(info.thinking_level)
        self.assertEqual(info.describe(), "Model: gpt-5")


if __name__ == "__main__":
    unittest.main()
