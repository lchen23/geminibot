from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.config import AppConfig
from app.dispatcher import Dispatcher, IncomingMessage


class DispatcherWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.config = AppConfig(
            feishu_app_id="app_id",
            feishu_app_secret="app_secret",
            gemini_api_key="",
            ai_provider="claude",
            gemini_cli_path="gemini",
            claude_cli_path="claude",
            bot_name="GeminiBot",
            default_timezone="UTC",
            workspace_root=root / "workspaces",
            data_root=root / "data",
            poll_interval_seconds=30,
            recent_summary_days=7,
            card_footer_enabled=True,
            log_level="INFO",
        )
        self.config.ensure_directories()
        self.memory_worker = MagicMock()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _message(self, text: str) -> IncomingMessage:
        return IncomingMessage(
            message_id="msg-1",
            chat_id="chat-1",
            user_id="user-1",
            conversation_id="conv-1",
            text=text,
            sent_at="2026-04-03T00:00:00+00:00",
        )

    def _markdown_content(self, response: dict) -> str:
        return response["body"]["elements"][0]["content"]

    def test_remember_submits_memory_save_and_reply_log(self) -> None:
        with patch("app.dispatcher.GeminiAgentEngine"):
            dispatcher = Dispatcher(config=self.config, memory_worker=self.memory_worker)

        response = dispatcher.handle(self._message("/remember Prefer concise replies"))

        self.memory_worker.submit_save_memory_note.assert_called_once_with("conv-1", "Prefer concise replies")
        self.memory_worker.submit_append_daily_log.assert_called_once_with(
            conversation_id="conv-1",
            user_text="/remember Prefer concise replies",
            assistant_text="Noted: Prefer concise replies",
        )
        self.assertIn("Noted: Prefer concise replies", self._markdown_content(response))

    def test_clear_submits_memory_consolidation_then_clears_agent_context(self) -> None:
        with patch("app.dispatcher.GeminiAgentEngine") as agent_cls:
            dispatcher = Dispatcher(config=self.config, memory_worker=self.memory_worker)

        response = dispatcher.handle(self._message("/clear"))

        self.memory_worker.submit_consolidate_workspace_memory.assert_called_once_with("conv-1")
        dispatcher.agent.clear_conversation.assert_called_once_with("conv-1")
        self.memory_worker.submit_append_daily_log.assert_called_once_with(
            conversation_id="conv-1",
            user_text="/clear",
            assistant_text="Conversation context cleared. Memory consolidation was scheduled.",
        )
        self.assertIn("Memory consolidation was scheduled.", self._markdown_content(response))
        self.assertIsNotNone(agent_cls)

    def test_normal_message_submits_reply_log_after_agent_run(self) -> None:
        with patch("app.dispatcher.GeminiAgentEngine") as agent_cls:
            agent_cls.return_value.run.return_value.text = "Hello from agent"
            dispatcher = Dispatcher(config=self.config, memory_worker=self.memory_worker)

        response = dispatcher.handle(self._message("hello"))

        dispatcher.agent.run.assert_called_once()
        self.memory_worker.submit_append_daily_log.assert_called_once_with(
            conversation_id="conv-1",
            user_text="hello",
            assistant_text="Hello from agent",
        )
        self.assertIn("Hello from agent", self._markdown_content(response))


if __name__ == "__main__":
    unittest.main()
