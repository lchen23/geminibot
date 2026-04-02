from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.config import AppConfig

SUMMARY_FALLBACK_PREFIX = "Semantic summary unavailable"
MAX_SEMANTIC_SUMMARY_BULLETS = 5
MAX_LONG_TERM_NOTES = 5
MAX_LOW_CONFIDENCE_SAVED_NOTES = 20
CONTEXT_NOTE_TTL_DAYS = 7
MEMORY_SECTIONS = ("User Preferences", "Stable Facts", "Saved Notes")
SEMANTIC_DEDUPE_SECTIONS = {"User Preferences", "Stable Facts"}
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
CONTEXT_HINTS = (
    "currently",
    "current",
    "working on",
    "right now",
    "this week",
    "this month",
    "today",
    "temporary",
    "for now",
    "in progress",
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
    metadata_updates = {name: {} for name in MEMORY_SECTIONS}
    section_seen = {
        name: {_normalize_item(item) for item in items}
        for name, items in memory_sections.items()
    }

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
                metadata_updates[target_section][normalized] = _build_note_metadata(
                    normalized,
                    section_name=target_section,
                    source=f"summary:{log_file.stem}",
                )

    if summary_sections:
        summary_file.write_text("\n\n".join(summary_sections).rstrip() + "\n", encoding="utf-8")

    if memory_file.exists() or any(items for items in memory_sections.values()):
        if not memory_file.exists():
            memory_file.write_text("# Memory\n\n## User Preferences\n\n## Stable Facts\n\n## Saved Notes\n", encoding="utf-8")
        _rewrite_memory_sections(
            memory_file,
            memory_sections,
            config=config,
            workspace=workspace,
            metadata_updates=metadata_updates,
        )


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
            env=_build_cli_environment(config, workspace),
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


def _build_cli_environment(config: AppConfig, workspace: Path) -> dict[str, str]:
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
    if _is_context_note(lowered):
        return "Saved Notes"
    if any(hint in lowered for hint in PREFERENCE_HINTS):
        return "User Preferences"
    if any(hint in lowered for hint in STABLE_FACT_HINTS):
        return "Stable Facts"
    return "Saved Notes"


def _build_note_metadata(
    note: str,
    *,
    section_name: str,
    source: str,
    created_at: str | None = None,
) -> dict[str, str]:
    normalized = _normalize_item(note)
    return {
        "created_at": created_at or _now_isoformat(),
        "source": _normalize_item(source) or "unknown",
        "kind": _classify_memory_kind(normalized, section_name=section_name, source=source),
    }


def _classify_memory_kind(note: str, *, section_name: str, source: str) -> str:
    lowered = _normalize_item(note).lower()
    if _is_context_note(lowered):
        return "context"
    if section_name == "User Preferences":
        return "preference"
    if section_name == "Stable Facts":
        return "fact"
    if source.startswith("summary:"):
        return "low_confidence"
    return "note"


def _is_context_note(note: str) -> bool:
    lowered = _normalize_item(note).lower()
    return any(hint in lowered for hint in CONTEXT_HINTS)


def _semantic_dedupe_entries(
    section_name: str,
    entries: list[dict[str, str]],
    *,
    config: AppConfig | None,
    workspace: Path | None,
) -> list[dict[str, str]]:
    deduped = _exact_dedupe_entries(entries)
    if section_name not in SEMANTIC_DEDUPE_SECTIONS or len(deduped) < 2 or config is None or workspace is None:
        return deduped

    merged: list[dict[str, str]] = []
    for entry in deduped:
        if not merged:
            merged.append(entry)
            continue

        decision = _semantic_duplicate_decision(section_name, [item["content"] for item in merged], entry["content"], config, workspace)
        if decision is None:
            merged.append(entry)
            continue

        duplicate_of = decision.get("duplicate_of")
        canonical = _normalize_item(decision.get("canonical") or "")
        if duplicate_of is None:
            merged.append(entry)
            continue
        if not isinstance(duplicate_of, int) or duplicate_of < 0 or duplicate_of >= len(merged):
            merged.append(entry)
            continue

        merged_entry = _merge_note_entries(merged[duplicate_of], entry)
        merged_entry["content"] = canonical or merged_entry["content"]
        merged[duplicate_of] = merged_entry
    return _exact_dedupe_entries(merged)


def _semantic_duplicate_decision(
    section_name: str,
    existing_items: list[str],
    candidate: str,
    config: AppConfig,
    workspace: Path,
) -> dict[str, object] | None:
    cli_path = config.selected_cli_path.strip()
    if not cli_path or shutil.which(cli_path) is None:
        return None

    prompt = _build_dedupe_prompt(section_name, existing_items, candidate)
    command = [cli_path, "-p", prompt, "--output-format", "json"]

    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            check=False,
            capture_output=True,
            text=True,
            env=_build_cli_environment(config, workspace),
        )
    except OSError:
        return None

    text = _extract_cli_text(config.ai_provider, completed.stdout.strip(), completed.stderr.strip())
    if completed.returncode != 0 or not text:
        return None

    return _parse_dedupe_decision(text)


def _build_dedupe_prompt(section_name: str, existing_items: list[str], candidate: str) -> str:
    existing_json = json.dumps(existing_items, ensure_ascii=False)
    candidate_json = json.dumps(candidate, ensure_ascii=False)
    return f"""You are deduplicating long-term memory entries for the section {section_name}.

Return JSON only with this schema:
{{"duplicate_of": <integer index or null>, "canonical": <string>}}

Rules:
- Treat entries as duplicates only if they express the same durable meaning.
- Do not merge loosely related statements.
- Prefer the more informative wording when two entries are duplicates.
- Keep canonical wording concise and factual.
- If the candidate is not a duplicate of any existing item, return {{"duplicate_of": null, "canonical": ""}}.

Existing items JSON:
{existing_json}

Candidate JSON:
{candidate_json}
"""


def _parse_dedupe_decision(text: str) -> dict[str, object] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = [line for line in cleaned.splitlines() if not line.startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    duplicate_of = payload.get("duplicate_of")
    canonical = payload.get("canonical", "")
    if duplicate_of is not None and not isinstance(duplicate_of, int):
        return None
    if canonical is not None and not isinstance(canonical, str):
        return None

    return {
        "duplicate_of": duplicate_of,
        "canonical": canonical or "",
    }


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


def _rewrite_memory_sections(
    memory_file: Path,
    sections: dict[str, list[str]],
    *,
    config: AppConfig | None,
    workspace: Path | None,
    metadata_updates: dict[str, dict[str, dict[str, str]]] | None = None,
) -> None:
    if not memory_file.exists():
        return

    original = memory_file.read_text(encoding="utf-8")
    existing_metadata = _load_memory_metadata(memory_file)
    fallback_created_at = _file_created_at(memory_file)
    serialized_metadata: dict[str, list[dict[str, str]]] = {}

    lines = ["# Memory", ""]
    for section_name in MEMORY_SECTIONS:
        entries = _build_section_entries(
            section_name,
            sections.get(section_name, []),
            existing_metadata.get(section_name, []),
            (metadata_updates or {}).get(section_name, {}),
            fallback_created_at,
        )
        entries = _semantic_dedupe_entries(section_name, entries, config=config, workspace=workspace)
        entries = _apply_retention_policy(section_name, entries)
        serialized_metadata[section_name] = entries
        lines.append(f"## {section_name}")
        lines.extend(f"- {entry['content']}" for entry in entries)
        lines.append("")

    for section_name, items in _extract_extra_sections(original).items():
        lines.append(f"## {section_name}")
        lines.extend(f"- {item}" for item in _exact_dedupe(items))
        lines.append("")

    memory_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    _write_memory_metadata(memory_file, serialized_metadata)


def _build_section_entries(
    section_name: str,
    items: list[str],
    existing_entries: list[dict[str, str]],
    metadata_updates: dict[str, dict[str, str]],
    fallback_created_at: str,
) -> list[dict[str, str]]:
    existing_by_content = {
        _normalize_item(entry.get("content", "")): entry
        for entry in existing_entries
        if _normalize_item(entry.get("content", ""))
    }
    entries: list[dict[str, str]] = []
    for item in items:
        normalized = _normalize_item(item)
        if not normalized:
            continue
        metadata = metadata_updates.get(normalized) or existing_by_content.get(normalized)
        entries.append(_normalize_note_entry(section_name, normalized, metadata, fallback_created_at))
    return entries


def _normalize_note_entry(
    section_name: str,
    content: str,
    metadata: dict[str, str] | None,
    fallback_created_at: str,
) -> dict[str, str]:
    normalized_content = _normalize_item(content)
    raw_source = _normalize_item((metadata or {}).get("source", "")) or "legacy"
    raw_created_at = (metadata or {}).get("created_at") or fallback_created_at
    created_at = _normalize_timestamp(raw_created_at) or fallback_created_at
    kind = _normalize_item((metadata or {}).get("kind", "")) or _classify_memory_kind(
        normalized_content,
        section_name=section_name,
        source=raw_source,
    )
    return {
        "content": normalized_content,
        "created_at": created_at,
        "source": raw_source,
        "kind": kind,
    }


def _apply_retention_policy(section_name: str, entries: list[dict[str, str]]) -> list[dict[str, str]]:
    retained = [entry for entry in entries if not _should_drop_entry(entry)]
    if section_name != "Saved Notes":
        return retained

    low_confidence = [entry for entry in retained if entry["kind"] == "low_confidence"]
    if len(low_confidence) <= MAX_LOW_CONFIDENCE_SAVED_NOTES:
        return retained

    keep_contents = {
        entry["content"]
        for entry in sorted(low_confidence, key=_entry_sort_key, reverse=True)[:MAX_LOW_CONFIDENCE_SAVED_NOTES]
    }
    return [
        entry
        for entry in retained
        if entry["kind"] != "low_confidence" or entry["content"] in keep_contents
    ]


def _should_drop_entry(entry: dict[str, str]) -> bool:
    if entry.get("kind") != "context":
        return False
    created_at = _parse_timestamp(entry.get("created_at", ""))
    if created_at is None:
        return False
    return created_at < datetime.now(timezone.utc) - timedelta(days=CONTEXT_NOTE_TTL_DAYS)


def _entry_sort_key(entry: dict[str, str]) -> datetime:
    return _parse_timestamp(entry.get("created_at", "")) or datetime.fromtimestamp(0, tz=timezone.utc)


def _merge_note_entries(existing: dict[str, str], candidate: dict[str, str]) -> dict[str, str]:
    merged = dict(existing)
    if _entry_sort_key(candidate) >= _entry_sort_key(existing):
        merged["created_at"] = candidate["created_at"]
        if candidate.get("source") and candidate["source"] != "legacy":
            merged["source"] = candidate["source"]
    if merged.get("kind") in {"legacy", "note"} and candidate.get("kind"):
        merged["kind"] = candidate["kind"]
    return merged


def _exact_dedupe_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: dict[str, int] = {}
    for entry in entries:
        content = _normalize_item(entry.get("content", ""))
        if not content:
            continue
        normalized_entry = dict(entry)
        normalized_entry["content"] = content
        index = seen.get(content)
        if index is None:
            seen[content] = len(deduped)
            deduped.append(normalized_entry)
            continue
        deduped[index] = _merge_note_entries(deduped[index], normalized_entry)
    return deduped


def _metadata_file(memory_file: Path) -> Path:
    return memory_file.with_name("MEMORY.meta.json")


def _load_memory_metadata(memory_file: Path) -> dict[str, list[dict[str, str]]]:
    metadata_file = _metadata_file(memory_file)
    if not metadata_file.exists():
        return {name: [] for name in MEMORY_SECTIONS}

    try:
        payload = json.loads(metadata_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {name: [] for name in MEMORY_SECTIONS}

    if not isinstance(payload, dict):
        return {name: [] for name in MEMORY_SECTIONS}

    sections = payload.get("sections")
    if not isinstance(sections, dict):
        return {name: [] for name in MEMORY_SECTIONS}

    loaded: dict[str, list[dict[str, str]]] = {name: [] for name in MEMORY_SECTIONS}
    for section_name in MEMORY_SECTIONS:
        raw_entries = sections.get(section_name, [])
        if not isinstance(raw_entries, list):
            continue
        for raw_entry in raw_entries:
            if not isinstance(raw_entry, dict):
                continue
            content = _normalize_item(str(raw_entry.get("content", "")))
            if not content:
                continue
            loaded[section_name].append(
                {
                    "content": content,
                    "created_at": str(raw_entry.get("created_at", "")),
                    "source": _normalize_item(str(raw_entry.get("source", ""))),
                    "kind": _normalize_item(str(raw_entry.get("kind", ""))),
                }
            )
    return loaded


def _write_memory_metadata(memory_file: Path, sections: dict[str, list[dict[str, str]]]) -> None:
    payload = {
        "version": 1,
        "sections": {
            section_name: [
                {
                    "content": entry["content"],
                    "created_at": entry["created_at"],
                    "source": entry["source"],
                    "kind": entry["kind"],
                }
                for entry in entries
            ]
            for section_name, entries in sections.items()
        },
    }
    _metadata_file(memory_file).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _file_created_at(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return _now_isoformat()


def _now_isoformat() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_timestamp(value: str) -> str | None:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return None
    return parsed.isoformat(timespec="seconds")


def _parse_timestamp(value: str) -> datetime | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _exact_dedupe(items: list[str]) -> list[str]:
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
