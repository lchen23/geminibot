from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.config import AppConfig
from app.memory.consolidate import (
    SummaryGenerationResult,
    _apply_retention_policy,
    _fallback_summary,
    _load_memory_metadata,
    _semantic_dedupe_entries,
    consolidate_workspace_memory,
)
from app.memory.store import MemoryStore


class MemoryRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.config = AppConfig(
            feishu_app_id="",
            feishu_app_secret="",
            gemini_api_key="",
            ai_provider="claude",
            gemini_cli_path="__missing_gemini_cli__",
            claude_cli_path="__missing_claude_cli__",
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
        self.conversation_id = "conv-1"
        self.workspace = self.store.get_workspace(self.conversation_id)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_remember_writes_section_kind_and_source_metadata(self) -> None:
        with patch("app.memory.consolidate._semantic_note_classification_decision", return_value=None):
            self.store.save_memory_note(self.conversation_id, "Prefer concise release summaries.")

        memory_text = (self.workspace / "MEMORY.md").read_text(encoding="utf-8")
        metadata = _load_memory_metadata(self.workspace / "MEMORY.md")

        self.assertIn("## User Preferences", memory_text)
        self.assertIn("- Prefer concise release summaries.", memory_text)
        matching_entries = [
            entry for entry in metadata["User Preferences"] if entry["content"] == "Prefer concise release summaries."
        ]

        self.assertEqual(len(matching_entries), 1)
        self.assertEqual(
            {**matching_entries[0], "created_at": "<dynamic>"},
            {
                "content": "Prefer concise release summaries.",
                "created_at": "<dynamic>",
                "source": "remember",
                "section": "User Preferences",
                "kind": "preference",
                "confidence": "0.7",
                "ttl_days": "",
            },
        )

    def test_semantic_dedupe_keeps_canonical_entry(self) -> None:
        entries = [
            {
                "content": "Prefer concise responses.",
                "created_at": "2026-04-01T10:00:00+00:00",
                "source": "remember",
                "section": "User Preferences",
                "kind": "preference",
                "confidence": "0.6",
                "ttl_days": "",
            },
            {
                "content": "The user prefers concise responses.",
                "created_at": "2026-04-02T10:00:00+00:00",
                "source": "summary:2026-04-02",
                "section": "User Preferences",
                "kind": "preference",
                "confidence": "0.9",
                "ttl_days": "",
            },
        ]

        with patch(
            "app.memory.consolidate._semantic_duplicate_decision",
            return_value={"duplicate_of": 0, "canonical": "Prefer concise responses."},
        ):
            deduped = _semantic_dedupe_entries(
                "User Preferences",
                entries,
                config=self.config,
                workspace=self.workspace,
            )

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["content"], "Prefer concise responses.")
        self.assertEqual(deduped[0]["kind"], "preference")
        self.assertEqual(deduped[0]["confidence"], "0.9")
        self.assertEqual(deduped[0]["created_at"], "2026-04-02T10:00:00+00:00")

    def test_context_ttl_drops_only_expired_entries(self) -> None:
        retained = _apply_retention_policy(
            "Saved Notes",
            [
                {
                    "content": "Temporary release focus.",
                    "created_at": "2026-03-20T00:00:00+00:00",
                    "source": "remember",
                    "section": "Saved Notes",
                    "kind": "context",
                    "confidence": "0.8",
                    "ttl_days": "7",
                },
                {
                    "content": "Current release owner is Liang.",
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "source": "remember",
                    "section": "Saved Notes",
                    "kind": "context",
                    "confidence": "0.8",
                    "ttl_days": "7",
                },
                {
                    "content": "Release notes follow the release workflow.",
                    "created_at": "2026-03-20T00:00:00+00:00",
                    "source": "remember",
                    "section": "Saved Notes",
                    "kind": "note",
                    "confidence": "0.8",
                    "ttl_days": "",
                },
            ],
        )

        retained_contents = {entry["content"] for entry in retained}
        self.assertNotIn("Temporary release focus.", retained_contents)
        self.assertIn("Current release owner is Liang.", retained_contents)
        self.assertIn("Release notes follow the release workflow.", retained_contents)

    def test_low_confidence_trim_only_applies_to_saved_notes(self) -> None:
        saved_notes = [
            {
                "content": f"Weak saved note {index}",
                "created_at": (datetime(2026, 4, 2, tzinfo=timezone.utc) + timedelta(minutes=index)).isoformat(timespec="seconds"),
                "source": "summary:2026-04-02",
                "section": "Saved Notes",
                "kind": "low_confidence",
                "confidence": "0.4",
                "ttl_days": "",
            }
            for index in range(25)
        ]
        saved_notes.append(
            {
                "content": "High value saved note",
                "created_at": "2026-04-02T12:00:00+00:00",
                "source": "remember",
                "section": "Saved Notes",
                "kind": "note",
                "confidence": "0.9",
                "ttl_days": "",
            }
        )
        stable_facts = [
            {
                "content": f"Fact {index}",
                "created_at": "2026-04-02T12:00:00+00:00",
                "source": "remember",
                "section": "Stable Facts",
                "kind": "low_confidence",
                "confidence": "0.4",
                "ttl_days": "",
            }
            for index in range(25)
        ]

        trimmed_saved_notes = _apply_retention_policy("Saved Notes", saved_notes)
        untouched_facts = _apply_retention_policy("Stable Facts", stable_facts)

        low_conf_saved = [entry for entry in trimmed_saved_notes if entry["kind"] == "low_confidence"]
        self.assertEqual(len(low_conf_saved), 20)
        self.assertIn("High value saved note", {entry["content"] for entry in trimmed_saved_notes})
        self.assertEqual(len(untouched_facts), 25)

    def test_search_keeps_memory_above_summary_above_logs(self) -> None:
        (self.workspace / "MEMORY.md").write_text(
            "# Memory\n\n"
            "## User Preferences\n"
            "- Prefer concise release notes.\n\n"
            "## Stable Facts\n"
            "- Release workflow owns release notes.\n\n"
            "## Saved Notes\n"
            "- Release notes are shared after approval.\n",
            encoding="utf-8",
        )
        (self.workspace / "MEMORY.meta.json").write_text(
            """
{
  "version": 1,
  "sections": {
    "User Preferences": [
      {
        "content": "Prefer concise release notes.",
        "created_at": "2026-04-02T12:00:00+00:00",
        "source": "remember",
        "section": "User Preferences",
        "kind": "preference",
        "confidence": "0.95",
        "ttl_days": ""
      }
    ],
    "Stable Facts": [
      {
        "content": "Release workflow owns release notes.",
        "created_at": "2026-04-02T12:00:00+00:00",
        "source": "remember",
        "section": "Stable Facts",
        "kind": "fact",
        "confidence": "0.85",
        "ttl_days": ""
      }
    ],
    "Saved Notes": [
      {
        "content": "Release notes are shared after approval.",
        "created_at": "2026-04-02T12:00:00+00:00",
        "source": "remember",
        "section": "Saved Notes",
        "kind": "note",
        "confidence": "0.7",
        "ttl_days": ""
      }
    ]
  }
}
""".strip()
            + "\n",
            encoding="utf-8",
        )
        summaries_dir = self.workspace / "summaries"
        summaries_dir.mkdir(parents=True, exist_ok=True)
        (summaries_dir / "2026-04-02.md").write_text(
            "## 2026-04-02\n"
            "### Semantic Summary\n"
            "- Release notes were reviewed for concise wording.\n"
            "### Potential Long-Term Notes\n"
            "- None\n",
            encoding="utf-8",
        )
        logs_dir = self.workspace / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "2026-04-02.md").write_text(
            "### 12:00:00\n"
            "**Q:** Please tighten the release notes wording.\n\n"
            "**A:** I made the release notes concise.\n",
            encoding="utf-8",
        )

        results = self.store.search(self.conversation_id, "concise release notes", limit=5)

        self.assertTrue(results[0].startswith("MEMORY.md:"))
        summary_index = next(index for index, item in enumerate(results) if item.startswith("summaries/"))
        log_index = next(index for index, item in enumerate(results) if item.startswith("logs/"))
        self.assertLess(summary_index, log_index)

    def test_consolidate_preserves_existing_valid_summary_when_fallback_runs(self) -> None:
        logs_dir = self.workspace / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "2026-04-01.md").write_text(
            "### 09:00:00\n"
            "**Q:** Please remember the release style.\n\n"
            "**A:** Stored the release style preference.\n",
            encoding="utf-8",
        )
        summary_file = self.workspace / "summaries" / f"{date.today().isoformat()}.md"
        summary_file.parent.mkdir(parents=True, exist_ok=True)
        summary_file.write_text(
            "## 2026-04-01\n"
            "### Semantic Summary\n"
            "- Stored a durable release style preference.\n"
            "### Potential Long-Term Notes\n"
            "- Prefer concise release notes.\n",
            encoding="utf-8",
        )

        with patch(
            "app.memory.consolidate._generate_semantic_summary",
            return_value=SummaryGenerationResult(
                text=_fallback_summary("2026-04-01", (logs_dir / "2026-04-01.md").read_text(encoding="utf-8"), reason="cli failed"),
                fallback_reason="cli failed",
            ),
        ):
            consolidate_workspace_memory(self.workspace, config=self.config)

        rewritten = summary_file.read_text(encoding="utf-8")
        self.assertIn("- Stored a durable release style preference.", rewritten)
        self.assertNotIn("Semantic summary unavailable", rewritten)


if __name__ == "__main__":
    unittest.main()
