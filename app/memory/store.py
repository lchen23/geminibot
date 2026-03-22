from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from app.config import AppConfig


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
        memory_file = self.get_workspace(conversation_id) / "MEMORY.md"
        previous = self.read_memory(conversation_id)
        if content not in previous:
            memory_file.write_text(previous.rstrip() + f"\n- {content}\n", encoding="utf-8")

    def read_memory(self, conversation_id: str) -> str:
        memory_file = self.get_workspace(conversation_id) / "MEMORY.md"
        if memory_file.exists():
            return memory_file.read_text(encoding="utf-8")
        return "# Memory\n"

    def rewrite_memory(self, conversation_id: str, lines: list[str]) -> None:
        memory_file = self.get_workspace(conversation_id) / "MEMORY.md"
        deduped = []
        seen: set[str] = set()
        for line in lines:
            cleaned = line.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            deduped.append(cleaned)
        body = "\n".join(f"- {line}" for line in deduped)
        memory_file.write_text(f"# Memory\n\n{body}\n" if body else "# Memory\n", encoding="utf-8")

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
        lowered = query.lower()
        matches: list[str] = []
        for file_path in self._iter_memory_files(workspace):
            for line in file_path.read_text(encoding="utf-8").splitlines():
                if lowered in line.lower():
                    matches.append(f"{file_path.name}: {line.strip()}")
                    if len(matches) >= limit:
                        return matches
        return matches

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

    def _iter_memory_files(self, workspace: Path) -> list[Path]:
        files: list[Path] = []
        memory_file = workspace / "MEMORY.md"
        if memory_file.exists():
            files.append(memory_file)
        for directory in [workspace / "summaries", workspace / "logs"]:
            if directory.exists():
                files.extend(sorted(directory.glob("*.md"), reverse=True))
        return files
