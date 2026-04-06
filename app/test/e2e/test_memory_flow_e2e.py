from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import AppConfig
from app.dispatcher import Dispatcher
from app.gateway.feishu import FeishuGateway
from app.memory.store import MemoryStore
from app.memory.worker import MemoryWorker


def _load_repo_env() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[3] / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _setting(name: str, repo_env: dict[str, str], default: str = "") -> str:
    return os.getenv(name, repo_env.get(name, default))


class RealMemoryFlowE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        repo_env = _load_repo_env()
        if not _setting("GEMINIBOT_E2E_REAL", repo_env):
            raise unittest.SkipTest("Set GEMINIBOT_E2E_REAL=1 to run real Feishu + Gemini E2E tests.")

        cls.repo_env = repo_env
        cls.chat_id = os.getenv("GEMINIBOT_E2E_FEISHU_CHAT_ID", "")
        cls.user_id = os.getenv("GEMINIBOT_E2E_FEISHU_USER_ID", "")
        cls.conversation_id = os.getenv("GEMINIBOT_E2E_CONVERSATION_ID", cls.chat_id)

        missing = [
            name
            for name, value in {
                "FEISHU_APP_ID": _setting("FEISHU_APP_ID", repo_env),
                "FEISHU_APP_SECRET": _setting("FEISHU_APP_SECRET", repo_env),
                "GEMINI_CLI_PATH": _setting("GEMINI_CLI_PATH", repo_env, "gemini"),
                "GEMINIBOT_E2E_FEISHU_CHAT_ID": cls.chat_id,
                "GEMINIBOT_E2E_FEISHU_USER_ID": cls.user_id,
            }.items()
            if not value
        ]
        if missing:
            raise unittest.SkipTest(f"Missing real E2E settings: {', '.join(missing)}")

        gemini_cli_path = _setting("GEMINI_CLI_PATH", repo_env, "gemini")
        if shutil.which(gemini_cli_path) is None:
            raise unittest.SkipTest(f"Gemini CLI not found on PATH: {gemini_cli_path}")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.config = AppConfig(
            feishu_app_id=_setting("FEISHU_APP_ID", self.repo_env),
            feishu_app_secret=_setting("FEISHU_APP_SECRET", self.repo_env),
            gemini_api_key=_setting("GEMINI_API_KEY", self.repo_env),
            ai_provider="gemini",
            gemini_cli_path=_setting("GEMINI_CLI_PATH", self.repo_env, "gemini"),
            claude_cli_path=_setting("CLAUDE_CLI_PATH", self.repo_env, "claude"),
            bot_name=_setting("BOT_NAME", self.repo_env, "GeminiBot"),
            default_timezone=_setting("DEFAULT_TIMEZONE", self.repo_env, "UTC"),
            app_root=root,
            poll_interval_seconds=5,
            recent_summary_days=int(_setting("RECENT_SUMMARY_DAYS", self.repo_env, "7")),
            card_footer_enabled=_setting("CARD_FOOTER_ENABLED", self.repo_env, "true").lower() == "true",
            log_level=_setting("LOG_LEVEL", self.repo_env, "INFO"),
        )
        self.config.ensure_directories()
        self.memory_store = MemoryStore(self.config)
        self.memory_worker = MemoryWorker(self.config)
        self.memory_worker.start()
        self.gateway = FeishuGateway(self.config, Dispatcher(config=self.config, memory_worker=self.memory_worker))
        self.gateway._get_tenant_access_token(force_refresh=True)

    def tearDown(self) -> None:
        self.memory_worker.stop()
        self.gateway.stop()
        self.temp_dir.cleanup()

    def _workspace(self) -> Path:
        return self.config.workspace_root / self.conversation_id

    def _today_log_file(self) -> Path:
        return self._workspace() / "logs" / f"{datetime.now(timezone.utc).date().isoformat()}.md"

    def _message_id(self) -> str:
        return f"e2e-{uuid4().hex}"

    def _send_via_gateway(self, text: str) -> dict:
        response = self.gateway.handle_text_message(
            message_id=self._message_id(),
            chat_id=self.chat_id,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            text=text,
        )
        self.assertIsNotNone(response)
        assert response is not None
        self.gateway.deliver(self.chat_id, response)
        return response

    def _markdown_content(self, response: dict) -> str:
        return response["body"]["elements"][0]["content"]

    def _wait_until(self, predicate, *, timeout: float = 120.0, interval: float = 0.5) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()

    def _read_json(self, path: Path) -> dict | list:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_real_gemini_reply_round_trip_through_feishu_gateway(self) -> None:
        token = f"REAL_E2E_REPLY_{uuid4().hex[:10]}"
        response = self._send_via_gateway(f"Reply with exactly this token and nothing else: {token}")

        content = self._markdown_content(response)
        logs_file = self._today_log_file()

        self.assertIn(token, content)
        self.assertTrue(self._wait_until(lambda: logs_file.exists() and token in logs_file.read_text(encoding="utf-8")))
        self.assertFalse((self.config.data_root / "unsent_messages.json").exists())

    def test_remember_command_persists_memory_through_real_feishu_flow(self) -> None:
        note = f"Prefer concise release notes {uuid4().hex[:10]}"
        response = self._send_via_gateway(f"/remember {note}")

        workspace = self._workspace()
        memory_file = workspace / "MEMORY.md"
        metadata_file = workspace / "MEMORY.meta.json"
        logs_file = self._today_log_file()

        self.assertIn(f"Noted: {note}", self._markdown_content(response))
        self.assertTrue(self._wait_until(lambda: memory_file.exists() and metadata_file.exists() and logs_file.exists()))

        memory_text = memory_file.read_text(encoding="utf-8")
        metadata = self._read_json(metadata_file)
        logs_text = logs_file.read_text(encoding="utf-8")
        matching_entries = [
            entry
            for entry in metadata["sections"]["User Preferences"]
            if entry["content"] == note
        ]

        self.assertIn(note, memory_text)
        self.assertEqual(len(matching_entries), 1)
        self.assertEqual(matching_entries[0]["source"], "remember")
        self.assertIn(f"**Q:** /remember {note}", logs_text)
        self.assertIn(f"**A:** Noted: {note}", logs_text)
        self.assertFalse((self.config.data_root / "unsent_messages.json").exists())

    def test_repeated_remember_commands_dedupe_memory_entries_through_real_feishu_flow(self) -> None:
        first = f"Prefer concise replies {uuid4().hex[:8]}"
        second = f"Prefer short status updates {uuid4().hex[:8]}"

        self._send_via_gateway(f"/remember {first}")
        self._send_via_gateway(f"/remember {first}")
        self._send_via_gateway(f"/remember {second}")

        memory_file = self._workspace() / "MEMORY.md"
        metadata_file = self._workspace() / "MEMORY.meta.json"
        self.assertTrue(
            self._wait_until(
                lambda: memory_file.exists()
                and metadata_file.exists()
                and second in memory_file.read_text(encoding="utf-8")
            )
        )

        memory_text = memory_file.read_text(encoding="utf-8")
        metadata = self._read_json(metadata_file)
        preferences = metadata["sections"]["User Preferences"]

        self.assertEqual(memory_text.count(first), 1)
        self.assertEqual(memory_text.count(second), 1)
        self.assertEqual(len([entry for entry in preferences if entry["content"] == first]), 1)
        self.assertEqual(len([entry for entry in preferences if entry["content"] == second]), 1)

    def test_clear_command_runs_real_gemini_summary_and_merges_memory(self) -> None:
        durable_note = f"Prefer concise and practical release notes {uuid4().hex[:8]}"
        self._send_via_gateway(
            "For long-term memory, a durable preference is: "
            f"{durable_note}. Acknowledge that you understand this preference."
        )

        response = self._send_via_gateway("/clear")
        summaries_dir = self._workspace() / "summaries"
        summary_file = summaries_dir / f"{datetime.now(timezone.utc).date().isoformat()}.md"
        metadata_file = self._workspace() / "MEMORY.meta.json"

        self.assertIn("Memory consolidation was scheduled.", self._markdown_content(response))
        self.assertTrue(
            self._wait_until(
                lambda: summary_file.exists()
                and metadata_file.exists()
                and any(
                    entry.get("source", "").startswith("summary:")
                    for entries in self._read_json(metadata_file).get("sections", {}).values()
                    for entry in entries
                )
            )
        )

        summary_text = summary_file.read_text(encoding="utf-8")
        metadata = self._read_json(metadata_file)

        self.assertIn("### Semantic Summary", summary_text)
        self.assertIn("### Potential Long-Term Notes", summary_text)
        self.assertTrue(
            any(
                entry.get("source", "").startswith("summary:")
                for entries in metadata.get("sections", {}).values()
                for entry in entries
            )
        )

    def test_search_returns_memory_hit_after_real_remember_write(self) -> None:
        note = f"Prefer concise release notes search-token-{uuid4().hex[:8]}"
        self._send_via_gateway(f"/remember {note}")
        memory_file = self._workspace() / "MEMORY.md"
        self.assertTrue(self._wait_until(lambda: memory_file.exists() and note in memory_file.read_text(encoding="utf-8")))

        results = self.memory_store.search(self.conversation_id, note, limit=3)

        self.assertTrue(results)
        self.assertTrue(results[0].startswith("MEMORY.md:"))
        self.assertIn(note, results[0])


if __name__ == "__main__":
    unittest.main()
