from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.config import AppConfig
from app.memory.consolidate import _build_note_metadata, _classify_long_term_note, _rewrite_memory_sections

REQUIRED_MEMORY_SECTIONS = (
    "User Preferences",
    "Stable Facts",
    "Saved Notes",
)

SECTION_ALIASES = {
    "Project Facts": "Stable Facts",
}

DEFAULT_MEMORY_ITEMS = {
    "User Preferences": ["Prefer concise and practical responses."],
    "Stable Facts": ["GeminiBot uses Gemini CLI Agent as the core reasoning runtime."],
    "Saved Notes": [],
}
LAYER_MEMORY = "memory"
LAYER_SUMMARY = "summary"
LAYER_LOG = "log"
HIGH_PRIORITY_SUMMARY_HEADINGS = {"### Semantic Summary", "### Potential Long-Term Notes"}
SECTION_RELEVANCE_BOOSTS = {
    "User Preferences": 80,
    "Stable Facts": 70,
    "Saved Notes": 90,
}
SUMMARY_RELEVANCE_BOOST = 75
LOG_RELEVANCE_BOOST = 10
MAX_LOG_RESULTS_WHEN_HIGHER_LAYERS_HIT = 2


@dataclass(slots=True)
class SearchHit:
    text: str
    score: int
    layer: str


class MemoryStore:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def append_daily_log(self, conversation_id: str, user_text: str, assistant_text: str) -> None:
        workspace = self.get_workspace(conversation_id)
        (workspace / "logs").mkdir(parents=True, exist_ok=True)
        log_file = workspace / "logs" / f"{datetime.now().date().isoformat()}.md"
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = (
            f"\n### {timestamp}\n"
            f"**Q:** {user_text}\n\n"
            f"**A:** {assistant_text}\n"
        )
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(entry)

    def save_memory_note(self, conversation_id: str, content: str) -> None:
        sections = self._read_memory_sections(conversation_id)
        cleaned = self._normalize_item(content)
        if not cleaned:
            return
        target_section = _classify_long_term_note(cleaned)
        existing = {self._normalize_item(item) for item in sections[target_section]}
        if cleaned not in existing:
            sections[target_section].append(cleaned)
            metadata_updates = {
                name: {} for name in REQUIRED_MEMORY_SECTIONS
            }
            metadata_updates[target_section][cleaned] = _build_note_metadata(
                cleaned,
                section_name=target_section,
                source="remember",
            )
            self._write_memory_sections(conversation_id, sections, metadata_updates=metadata_updates)

    def read_memory(self, conversation_id: str) -> str:
        memory_file = self.get_workspace(conversation_id) / "MEMORY.md"
        if memory_file.exists():
            return memory_file.read_text(encoding="utf-8")
        return self._serialize_memory_sections(self._default_memory_sections())

    def rewrite_memory(self, conversation_id: str, lines: list[str]) -> None:
        sections = self._read_memory_sections(conversation_id)
        deduped: list[str] = []
        seen: set[str] = set()
        for line in lines:
            cleaned = self._normalize_item(line)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            deduped.append(cleaned)
        sections["Saved Notes"] = deduped
        metadata_updates = {
            name: {} for name in REQUIRED_MEMORY_SECTIONS
        }
        for item in deduped:
            metadata_updates["Saved Notes"][item] = _build_note_metadata(
                item,
                section_name="Saved Notes",
                source="rewrite",
            )
        self._write_memory_sections(conversation_id, sections, metadata_updates=metadata_updates)

    def write_summary(self, conversation_id: str, summary_date: date, content: str) -> Path:
        workspace = self.get_workspace(conversation_id)
        summaries_dir = workspace / "summaries"
        summaries_dir.mkdir(parents=True, exist_ok=True)
        summary_file = summaries_dir / f"{summary_date.isoformat()}.md"
        summary_file.write_text(content.rstrip() + "\n", encoding="utf-8")
        return summary_file

    def read_recent_summaries(self, workspace: Path, days: int) -> str:
        summaries_dir = workspace / "summaries"
        if not summaries_dir.exists():
            return ""
        files = sorted(summaries_dir.glob("*.md"), reverse=True)[:days]
        return "\n\n".join(path.read_text(encoding="utf-8").strip() for path in files if path.exists())

    def search(self, conversation_id: str, query: str, limit: int = 10) -> list[str]:
        workspace = self.get_workspace(conversation_id)
        normalized_query = self._normalize_item(query)
        if not normalized_query or limit <= 0:
            return []

        memory_hits = self._search_memory_file(workspace, normalized_query)
        summary_hits = self._search_summary_files(workspace, normalized_query)
        higher_layer_hits = [*memory_hits, *summary_hits]
        higher_layer_count = len(higher_layer_hits)
        log_limit = max(0, limit - higher_layer_count)
        if higher_layer_count:
            log_limit = min(log_limit, MAX_LOG_RESULTS_WHEN_HIGHER_LAYERS_HIT)

        log_hits = self._search_log_files(workspace, normalized_query, limit=log_limit)
        ranked = sorted([*higher_layer_hits, *log_hits], key=lambda hit: (-hit.score, hit.text))
        return [hit.text for hit in ranked[:limit]]

    def list_by_date(self, conversation_id: str, start_date: str, end_date: str) -> list[str]:
        workspace = self.get_workspace(conversation_id)
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        results: list[str] = []
        for file_path in sorted((workspace / "logs").glob("*.md")):
            try:
                log_date = date.fromisoformat(file_path.stem)
            except ValueError:
                continue
            if start <= log_date <= end:
                results.append(file_path.read_text(encoding="utf-8").strip())
        return results

    def get_workspace(self, conversation_id: str) -> Path:
        workspace = self.config.workspace_root / conversation_id
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def _search_memory_file(self, workspace: Path, query: str) -> list[SearchHit]:
        memory_file = workspace / "MEMORY.md"
        if not memory_file.exists():
            return []

        hits: list[SearchHit] = []
        current_section = ""
        relative_name = memory_file.relative_to(workspace).as_posix()
        for raw_line in memory_file.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("## "):
                current_section = SECTION_ALIASES.get(stripped.removeprefix("## ").strip(), stripped.removeprefix("## ").strip())
                continue
            if not stripped.startswith("- "):
                continue
            content = self._normalize_item(stripped[2:])
            score = self._score_match(content, query)
            if score <= 0:
                continue
            section_boost = SECTION_RELEVANCE_BOOSTS.get(current_section, 50)
            hits.append(
                SearchHit(
                    text=f"{relative_name}: {stripped}",
                    score=score + section_boost,
                    layer=LAYER_MEMORY,
                )
            )
        return hits

    def _search_summary_files(self, workspace: Path, query: str) -> list[SearchHit]:
        summaries_dir = workspace / "summaries"
        if not summaries_dir.exists():
            return []

        hits: list[SearchHit] = []
        for file_path in sorted(summaries_dir.glob("*.md"), reverse=True):
            relative_name = file_path.relative_to(workspace).as_posix()
            current_heading = ""
            for raw_line in file_path.read_text(encoding="utf-8").splitlines():
                stripped = raw_line.strip()
                if stripped.startswith("### "):
                    current_heading = stripped
                    continue
                if not stripped.startswith("- "):
                    continue
                content = self._normalize_item(stripped[2:])
                score = self._score_match(content, query)
                if score <= 0:
                    continue
                heading_boost = SUMMARY_RELEVANCE_BOOST if current_heading in HIGH_PRIORITY_SUMMARY_HEADINGS else 40
                hits.append(
                    SearchHit(
                        text=f"{relative_name}: {stripped}",
                        score=score + heading_boost,
                        layer=LAYER_SUMMARY,
                    )
                )
        return hits

    def _search_log_files(self, workspace: Path, query: str, limit: int) -> list[SearchHit]:
        if limit <= 0:
            return []
        logs_dir = workspace / "logs"
        if not logs_dir.exists():
            return []

        hits: list[SearchHit] = []
        for file_path in sorted(logs_dir.glob("*.md"), reverse=True):
            relative_name = file_path.relative_to(workspace).as_posix()
            for raw_line in file_path.read_text(encoding="utf-8").splitlines():
                stripped = raw_line.strip()
                if not stripped or stripped.startswith("### "):
                    continue
                score = self._score_match(stripped, query)
                if score <= 0:
                    continue
                hits.append(
                    SearchHit(
                        text=f"{relative_name}: {stripped}",
                        score=score + LOG_RELEVANCE_BOOST,
                        layer=LAYER_LOG,
                    )
                )
        hits.sort(key=lambda hit: (-hit.score, hit.text))
        return hits[:limit]

    def _read_memory_sections(self, conversation_id: str) -> dict[str, list[str]]:
        text = self.read_memory(conversation_id)
        return self._parse_memory_sections(text)

    def _write_memory_sections(
        self,
        conversation_id: str,
        sections: dict[str, list[str]],
        metadata_updates: dict[str, dict[str, dict[str, str]]] | None = None,
    ) -> None:
        workspace = self.get_workspace(conversation_id)
        memory_file = workspace / "MEMORY.md"
        if not memory_file.exists():
            memory_file.write_text(self._serialize_memory_sections(self._default_memory_sections()), encoding="utf-8")
        _rewrite_memory_sections(
            memory_file,
            sections,
            config=self.config,
            workspace=workspace,
            metadata_updates=metadata_updates,
        )

    def _default_memory_sections(self) -> dict[str, list[str]]:
        return {name: list(items) for name, items in DEFAULT_MEMORY_ITEMS.items()}

    def _parse_memory_sections(self, text: str) -> dict[str, list[str]]:
        sections = self._default_memory_sections()
        extras: dict[str, list[str]] = {}
        current_section = "Saved Notes"

        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped == "# Memory":
                continue
            if stripped.startswith("## "):
                section_name = stripped.removeprefix("## ").strip()
                current_section = SECTION_ALIASES.get(section_name, section_name)
                target = sections if current_section in REQUIRED_MEMORY_SECTIONS else extras
                target.setdefault(current_section, [])
                continue
            if not stripped.startswith("- "):
                continue
            item = self._normalize_item(stripped[2:])
            if not item:
                continue
            target = sections if current_section in REQUIRED_MEMORY_SECTIONS else extras
            target.setdefault(current_section, []).append(item)

        for name, items in extras.items():
            sections[name] = items
        return sections

    def _serialize_memory_sections(self, sections: dict[str, list[str]]) -> str:
        ordered_names = [*REQUIRED_MEMORY_SECTIONS, *[name for name in sections if name not in REQUIRED_MEMORY_SECTIONS]]
        lines = ["# Memory", ""]

        for name in ordered_names:
            items = self._exact_dedupe(sections.get(name, []))
            lines.append(f"## {name}")
            lines.extend(f"- {item}" for item in items)
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _exact_dedupe(self, items: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            cleaned = self._normalize_item(item)
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            deduped.append(cleaned)
        return deduped

    def _score_match(self, text: str, query: str) -> int:
        normalized_text = self._normalize_item(text).lower()
        normalized_query = self._normalize_item(query).lower()
        if not normalized_text or not normalized_query:
            return 0

        query_tokens = [token for token in normalized_query.split(" ") if token]
        if not query_tokens:
            return 0

        score = 0
        if normalized_text == normalized_query:
            score += 150
        elif normalized_query in normalized_text:
            score += 90

        matched_tokens = sum(1 for token in query_tokens if token in normalized_text)
        if matched_tokens == 0:
            return 0

        score += matched_tokens * 20
        if matched_tokens == len(query_tokens):
            score += 40
        score += min(len(normalized_query), 40)
        return score

    def _normalize_item(self, value: str) -> str:
        return " ".join(value.strip().split())
