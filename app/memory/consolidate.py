from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.config import AppConfig

SUMMARY_FALLBACK_PREFIX = "Semantic summary unavailable"
MAX_SEMANTIC_SUMMARY_BULLETS = 5
MAX_LONG_TERM_NOTES = 5
MEMORY_SECTIONS = ("User Preferences", "Stable Facts", "Saved Notes")
PREFERENCE_HINTS = (
    "prefer",
    "prefers",
    "like",
    "likes",
    "dislike",
    "dislikes",
    "want",
    "wants",
    "avoid",
    "style",
    "tone",
    "concise",
    "practical",
)
STABLE_FACT_HINTS = (
    "geminibot",
    "project",
    "repo",
    "repository",
    "uses",
    "runs on",
    "default",
    "workspace",
    "memory layer",
    "scheduler",
    "feishu",
)


@dataclass(slots=True)
class SummaryGenerationResult:
    text: str
    fallback_reason: str | None = None

    @property
    def is_fallback(self) -> bool:
        return self.fallback_reason is not None


@dataclass(slots=True)
class ParsedSummary:
    log_date: str
    semantic_summary: list[str]
    potential_long_term_notes: list[str]

    def to_markdown(self) -> str:
        lines = [f"## {self.log_date}", "### Semantic Summary"]
        lines.extend(f"- {item}" for item in self.semantic_summary)
        lines.append("### Potential Long-Term Notes")
        if self.potential_long_term_notes:
            lines.extend(f"- {item}" for item in self.potential_long_term_notes)
        else:
            lines.append("- None")
        return "\n".join(lines)


def consolidate_workspace_memory(workspace: Path, config: AppConfig | None = None) -> None:
    logs_dir = workspace / "logs"
    if not logs_dir.exists():
        return

    summaries_dir = workspace / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_file = summaries_dir / f"{date.today().isoformat()}.md"
    existing_valid_summaries = _load_existing_valid_summaries(summary_file)

    summary_sections: list[str] = []
    memory_file = workspace / "MEMORY.md"
    memory_sections = _read_memory_sections(memory_file)
    section_seen = {
        name: {_normalize_item(item) for item in items}
        for name, items in memory_sections.items()
    }
    notes_changed = False

    for log_file in sorted(logs_dir.glob("*.md")):
        content = log_file.read_text(encoding="utf-8").strip()
        if not content:
            continue

        generation = _generate_semantic_summary(
            log_date=log_file.stem,
            log_content=content,
            workspace=workspace,
            config=config,
        )
        parsed = _parse_summary(log_file.stem, generation.text)

        if parsed is None:
            preserved = existing_valid_summaries.get(log_file.stem)
            if preserved is not None:
                summary_sections.append(preserved.to_markdown())
                continue
            summary_sections.append(_fallback_summary(log_file.stem, content, reason="invalid summary structure"))
            continue

        if generation.is_fallback:
            preserved = existing_valid_summaries.get(log_file.stem)
            if preserved is not None:
                summary_sections.append(preserved.to_markdown())
                continue
            summary_sections.append(generation.text)
            continue

        summary_sections.append(parsed.to_markdown())
        for candidate in parsed.potential_long_term_notes[:MAX_LONG_TERM_NOTES]:
            target_section = _classify_long_term_note(candidate)
            normalized = _normalize_item(candidate)
            if normalized and normalized not in section_seen[target_section]:
                memory_sections[target_section].append(normalized)
                section_seen[target_section].add(normalized)
                notes_changed = True

    if summary_sections:
        summary_file.write_text("\n\n".join(summary_sections).rstrip() + "\n", encoding="utf-8")

    if notes_changed:
        _rewrite_memory_sections(memory_file, memory_sections)


def _generate_semantic_summary(
    log_date: str,
    log_content: str,
    workspace: Path,
    config: AppConfig | None,
) -> SummaryGenerationResult:
    if config is None:
        return SummaryGenerationResult(
            text=_fallback_summary(log_date, log_content, reason="missing runtime config"),
            fallback_reason="missing runtime config",
        )

    cli_path = config.selected_cli_path.strip()
    if not cli_path or shutil.which(cli_path) is None:
        return SummaryGenerationResult(
            text=_fallback_summary(log_date, log_content, reason=f"{config.ai_provider} CLI not available"),
            fallback_reason=f"{config.ai_provider} CLI not available",
        )

    prompt = _build_summary_prompt(log_date, log_content)
    command = [cli_path, "-p", prompt, "--output-format", "json"]

    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            check=False,
            capture_output=True,
            text=True,
            env=_build_summary_environment(config, workspace),
        )
    except OSError as exc:
        return SummaryGenerationResult(
            text=_fallback_summary(log_date, log_content, reason=str(exc)),
            fallback_reason=str(exc),
        )

    text = _extract_cli_text(config.ai_provider, completed.stdout.strip(), completed.stderr.strip())
    if completed.returncode != 0 or not text:
        error_text = text or completed.stderr.strip() or f"{config.ai_provider} CLI exited with status {completed.returncode}"
        return SummaryGenerationResult(
            text=_fallback_summary(log_date, log_content, reason=error_text),
            fallback_reason=error_text,
        )

    normalized = text.strip()
    if not normalized.startswith(f"## {log_date}"):
        normalized = f"## {log_date}\n{normalized}"
    return SummaryGenerationResult(text=normalized)


def _build_summary_prompt(log_date: str, log_content: str) -> str:
    return f"""You are summarizing a single conversation log for long-term workspace memory.

Return markdown only. Use exactly this structure:
## {log_date}
### Semantic Summary
- <1-5 bullets capturing the user's goals, durable context, and outcomes>
### Potential Long-Term Notes
- <0-5 bullets for stable preferences, facts, or constraints worth saving>

Rules:
- Focus on durable information, not every turn.
- Do not repeat raw Q/A transcripts.
- Do not invent facts.
- Never output more than 5 bullets under either section.
- If there are no durable long-term notes, write exactly: - None
- Keep each bullet concise.

Conversation log:
{log_content}
"""


def _build_summary_environment(config: AppConfig, workspace: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GEMINIBOT_WORKSPACE": str(workspace),
            "GEMINIBOT_TIMEZONE": config.default_timezone,
        }
    )
    return env


def _extract_cli_text(provider: str, stdout: str, stderr: str) -> str:
    if not stdout:
        return stderr

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout

    if provider == "claude":
        result = data.get("result")
        if isinstance(result, str) and result.strip():
            return result.strip()
        return stderr or json.dumps(data, ensure_ascii=False)

    error = data.get("error")
    if isinstance(error, dict):
        return error.get("message") or json.dumps(error, ensure_ascii=False)
    response = data.get("response")
    if isinstance(response, str) and response.strip():
        return response.strip()
    return stderr or json.dumps(data, ensure_ascii=False)


def _parse_summary(log_date: str, text: str) -> ParsedSummary | None:
    semantic_summary: list[str] = []
    long_term_notes: list[str] = []
    current_section: str | None = None
    saw_semantic_heading = False
    saw_notes_heading = False

    for index, raw_line in enumerate(text.splitlines()):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if index == 0:
            if stripped != f"## {log_date}":
                return None
            continue
        if stripped == "### Semantic Summary":
            current_section = "semantic"
            saw_semantic_heading = True
            continue
        if stripped == "### Potential Long-Term Notes":
            current_section = "notes"
            saw_notes_heading = True
            continue
        if stripped.startswith("### "):
            return None
        if not stripped.startswith("- "):
            return None

        item = _normalize_item(stripped[2:])
        if not item:
            return None
        if item.lower().startswith(SUMMARY_FALLBACK_PREFIX.lower()):
            return None

        if current_section == "semantic":
            semantic_summary.append(item)
        elif current_section == "notes":
            if item.lower() != "none":
                long_term_notes.append(item)
        else:
            return None

    if not saw_semantic_heading or not saw_notes_heading:
        return None
    if not semantic_summary or len(semantic_summary) > MAX_SEMANTIC_SUMMARY_BULLETS:
        return None
    if len(long_term_notes) > MAX_LONG_TERM_NOTES:
        return None

    return ParsedSummary(
        log_date=log_date,
        semantic_summary=semantic_summary,
        potential_long_term_notes=long_term_notes,
    )


def _classify_long_term_note(note: str) -> str:
    lowered = _normalize_item(note).lower()
    if any(hint in lowered for hint in PREFERENCE_HINTS):
        return "User Preferences"
    if any(hint in lowered for hint in STABLE_FACT_HINTS):
        return "Stable Facts"
    return "Saved Notes"


def _load_existing_valid_summaries(summary_file: Path) -> dict[str, ParsedSummary]:
    if not summary_file.exists():
        return {}

    summaries: dict[str, ParsedSummary] = {}
    for block in _split_summary_blocks(summary_file.read_text(encoding="utf-8")):
        log_date = _extract_log_date(block)
        if not log_date:
            continue
        parsed = _parse_summary(log_date, block)
        if parsed is not None:
            summaries[log_date] = parsed
    return summaries


def _split_summary_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []

    for raw_line in text.splitlines():
        if raw_line.startswith("## ") and current:
            blocks.append("\n".join(current).strip())
            current = [raw_line]
        elif raw_line.startswith("## "):
            current = [raw_line]
        elif current:
            current.append(raw_line)

    if current:
        blocks.append("\n".join(current).strip())
    return blocks


def _extract_log_date(block: str) -> str | None:
    first_line = block.splitlines()[0].strip() if block.strip() else ""
    if not first_line.startswith("## "):
        return None
    return first_line.removeprefix("## ").strip() or None


def _fallback_summary(log_date: str, log_content: str, reason: str) -> str:
    questions: list[str] = []
    answers: list[str] = []
    for line in log_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("**Q:**"):
            questions.append(stripped.removeprefix("**Q:**").strip())
        elif stripped.startswith("**A:**"):
            answers.append(stripped.removeprefix("**A:**").strip())

    lines = [
        f"## {log_date}",
        "### Semantic Summary",
        f"- {SUMMARY_FALLBACK_PREFIX}: {reason}",
        f"- User messages: {len(questions)}",
        f"- Assistant replies: {len(answers)}",
    ]
    if questions:
        lines.append(f"- Latest user intent: {_truncate(questions[-1])}")
    if answers:
        lines.append(f"- Latest assistant outcome: {_truncate(answers[-1])}")
    lines.append("### Potential Long-Term Notes")
    lines.append("- None")
    return "\n".join(lines)


def _read_memory_sections(memory_file: Path) -> dict[str, list[str]]:
    sections = {name: [] for name in MEMORY_SECTIONS}
    if not memory_file.exists():
        return sections

    current_section = ""
    for raw_line in memory_file.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            current_section = stripped.removeprefix("## ").strip()
            if current_section not in sections:
                current_section = ""
            continue
        if stripped.startswith("- ") and current_section in sections:
            item = _normalize_item(stripped[2:])
            if item:
                sections[current_section].append(item)
    return sections


def _rewrite_memory_sections(memory_file: Path, sections: dict[str, list[str]]) -> None:
    if not memory_file.exists():
        return

    original = memory_file.read_text(encoding="utf-8")
    lines = ["# Memory", ""]
    for section_name in MEMORY_SECTIONS:
        lines.append(f"## {section_name}")
        lines.extend(f"- {item}" for item in _dedupe(sections.get(section_name, [])))
        lines.append("")

    for section_name, items in _extract_extra_sections(original).items():
        lines.append(f"## {section_name}")
        lines.extend(f"- {item}" for item in _dedupe(items))
        lines.append("")

    memory_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _extract_extra_sections(text: str) -> dict[str, list[str]]:
    extras: dict[str, list[str]] = {}
    current_section = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            section_name = stripped.removeprefix("## ").strip()
            current_section = "" if section_name in MEMORY_SECTIONS else section_name
            if current_section:
                extras.setdefault(current_section, [])
            continue
        if current_section and stripped.startswith("- "):
            item = _normalize_item(stripped[2:])
            if item:
                extras[current_section].append(item)
    return extras


def _dedupe(items: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = _normalize_item(item)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped


def _truncate(text: str, limit: int = 120) -> str:
    cleaned = _normalize_item(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _normalize_item(value: str) -> str:
    return " ".join(value.strip().split())
