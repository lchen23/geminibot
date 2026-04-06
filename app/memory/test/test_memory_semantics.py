from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.config import AppConfig
from app.memory.consolidate import (
    CONTEXT_NOTE_TTL_DAYS,
    _build_note_metadata,
    _fallback_note_classification,
    _parse_note_classification,
)
from app.memory.store import MemoryStore


class MemorySemanticsTests(unittest.TestCase):
    def test_parse_note_classification_accepts_structured_response(self) -> None:
        classification = _parse_note_classification(
            '{"section":"User Preferences","kind":"preference","confidence":0.91,"ttl_days":null}',
            source="remember",
        )

        self.assertIsNotNone(classification)
        assert classification is not None
        self.assertEqual(classification.section, "User Preferences")
        self.assertEqual(classification.kind, "preference")
        self.assertEqual(classification.confidence, 0.91)
        self.assertIsNone(classification.ttl_days)

    def test_parse_note_classification_normalizes_context_ttl(self) -> None:
        classification = _parse_note_classification(
            '{"section":"Saved Notes","kind":"context","confidence":0.83,"ttl_days":null}',
            source="summary:2026-04-02",
        )

        self.assertIsNotNone(classification)
        assert classification is not None
        self.assertEqual(classification.kind, "context")
        self.assertEqual(classification.ttl_days, CONTEXT_NOTE_TTL_DAYS)

    def test_parse_note_classification_demotes_weak_summary_note(self) -> None:
        classification = _parse_note_classification(
            '{"section":"Saved Notes","kind":"note","confidence":0.41,"ttl_days":null}',
            source="summary:2026-04-02",
        )

        self.assertIsNotNone(classification)
        assert classification is not None
        self.assertEqual(classification.kind, "low_confidence")

    def test_fallback_classification_marks_context_with_ttl(self) -> None:
        classification = _fallback_note_classification(
            "The user is currently working on the release branch.",
            source="remember",
            fallback_section=None,
        )

        self.assertEqual(classification.section, "Saved Notes")
        self.assertEqual(classification.kind, "context")
        self.assertEqual(classification.ttl_days, CONTEXT_NOTE_TTL_DAYS)

    def test_build_note_metadata_persists_confidence_and_ttl(self) -> None:
        classification = _fallback_note_classification(
            "The user is currently working on the release branch.",
            source="remember",
            fallback_section=None,
        )

        metadata = _build_note_metadata(
            "The user is currently working on the release branch.",
            classification=classification,
            source="remember",
        )

        self.assertEqual(metadata["section"], "Saved Notes")
        self.assertEqual(metadata["kind"], "context")
        self.assertEqual(metadata["confidence"], "0.65")
        self.assertEqual(metadata["ttl_days"], str(CONTEXT_NOTE_TTL_DAYS))


class MemorySearchRankingTests(unittest.TestCase):
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
            app_root=root,
            poll_interval_seconds=30,
            recent_summary_days=7,
            card_footer_enabled=True,
            log_level="INFO",
        )
        self.config.ensure_directories()
        self.store = MemoryStore(self.config)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_metadata_score_includes_confidence_boost(self) -> None:
        high = self.store._metadata_score(
            {
                "kind": "note",
                "source": "remember",
                "confidence": "0.95",
                "ttl_days": "",
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        low = self.store._metadata_score(
            {
                "kind": "note",
                "source": "remember",
                "confidence": "0.1",
                "ttl_days": "",
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )

        self.assertGreater(high, low)

    def test_metadata_score_penalizes_expired_context_ttl(self) -> None:
        now = datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc)
        fresh_created_at = (now - timedelta(days=1)).isoformat(timespec="seconds")
        expired_created_at = (now - timedelta(days=10)).isoformat(timespec="seconds")

        with patch("app.memory.store.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = now
            fresh = self.store._metadata_score(
                {
                    "kind": "context",
                    "source": "remember",
                    "confidence": "0.8",
                    "ttl_days": "7",
                    "created_at": fresh_created_at,
                }
            )
            expired = self.store._metadata_score(
                {
                    "kind": "context",
                    "source": "remember",
                    "confidence": "0.8",
                    "ttl_days": "7",
                    "created_at": expired_created_at,
                }
            )

        self.assertGreater(fresh, expired)

    def test_search_prefers_higher_confidence_memory_entry(self) -> None:
        workspace = self.store.get_workspace("conv-1")
        (workspace / "MEMORY.md").write_text(
            "# Memory\n\n"
            "## User Preferences\n"
            "- Prefer concise responses for release notes.\n\n"
            "## Stable Facts\n"
            "- Release notes are published from the release workflow.\n\n"
            "## Saved Notes\n"
            "- Release notes are concise and practical.\n",
            encoding="utf-8",
        )
        (workspace / "MEMORY.meta.json").write_text(
            """
{
  "version": 1,
  "sections": {
    "User Preferences": [
      {
        "content": "Prefer concise responses for release notes.",
        "created_at": "2026-04-01T12:00:00+00:00",
        "source": "remember",
        "section": "User Preferences",
        "kind": "preference",
        "confidence": "0.95",
        "ttl_days": ""
      }
    ],
    "Stable Facts": [
      {
        "content": "Release notes are published from the release workflow.",
        "created_at": "2026-04-01T12:00:00+00:00",
        "source": "remember",
        "section": "Stable Facts",
        "kind": "fact",
        "confidence": "0.6",
        "ttl_days": ""
      }
    ],
    "Saved Notes": [
      {
        "content": "Release notes are concise and practical.",
        "created_at": "2026-04-01T12:00:00+00:00",
        "source": "summary:2026-04-01",
        "section": "Saved Notes",
        "kind": "low_confidence",
        "confidence": "0.2",
        "ttl_days": ""
      }
    ]
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )

        results = self.store.search("conv-1", "concise release notes", limit=3)

        self.assertEqual(results[0], "MEMORY.md: - Prefer concise responses for release notes.")


if __name__ == "__main__":
    unittest.main()
