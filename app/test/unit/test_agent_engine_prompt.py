from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent.engine import GeminiAgentEngine
from app.config import AppConfig
from app.memory.store import MemoryStore


class AgentEnginePromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.config = AppConfig(
            feishu_app_id="",
            feishu_app_secret="",
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
        self.store = MemoryStore(self.config)
        self.engine = GeminiAgentEngine(config=self.config, memory_store=self.store)
        self.workspace = self.engine.workspace_manager.ensure_workspace("conv-1")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_build_system_prompt_reuses_cached_prompt_snapshot(self) -> None:
        (self.workspace / "AGENT.md").write_text("# Agent\nCached agent instructions.\n", encoding="utf-8")
        (self.workspace / "MEMORY.md").write_text(
            "# Memory\n\n"
            "## User Preferences\n"
            "- Prefer concise replies.\n\n"
            "## Stable Facts\n\n"
            "## Saved Notes\n",
            encoding="utf-8",
        )
        summaries_dir = self.workspace / "summaries"
        summaries_dir.mkdir(parents=True, exist_ok=True)
        (summaries_dir / "2026-04-03.md").write_text(
            "## 2026-04-03\n"
            "### Semantic Summary\n"
            "- First summary.\n"
            "### Potential Long-Term Notes\n"
            "- None\n",
            encoding="utf-8",
        )

        with patch.object(self.store, "read_memory", wraps=self.store.read_memory) as read_memory, patch.object(
            self.store,
            "read_recent_summaries",
            wraps=self.store.read_recent_summaries,
        ) as read_recent_summaries, patch.object(
            self.engine,
            "_read_tool_guide",
            wraps=self.engine._read_tool_guide,
        ) as read_tool_guide:
            first = self.engine._build_system_prompt(self.workspace)
            second = self.engine._build_system_prompt(self.workspace)

        self.assertEqual(first, second)
        self.assertEqual(read_memory.call_count, 1)
        self.assertEqual(read_recent_summaries.call_count, 1)
        self.assertEqual(read_tool_guide.call_count, 1)
        self.assertIn("## MEMORY.md", first)
        self.assertIn("## Recent Summaries", first)
        self.assertIn("Cached agent instructions.", first)

    def test_build_system_prompt_refreshes_when_static_prompt_files_change(self) -> None:
        agent_file = self.workspace / "AGENT.md"
        tool_guide = self.workspace / "tools" / "README.md"
        agent_file.write_text("# Agent\nFirst version.\n", encoding="utf-8")
        tool_guide.write_text("# Tools\nFirst guide.\n", encoding="utf-8")

        first = self.engine._build_system_prompt(self.workspace)

        agent_file.write_text("# Agent\nUpdated version.\n", encoding="utf-8")
        tool_guide.write_text("# Tools\nUpdated guide.\n", encoding="utf-8")

        second = self.engine._build_system_prompt(self.workspace)

        self.assertIn("First version.", first)
        self.assertIn("First guide.", first)
        self.assertIn("Updated version.", second)
        self.assertIn("Updated guide.", second)


if __name__ == "__main__":
    unittest.main()
