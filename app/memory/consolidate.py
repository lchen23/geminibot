from __future__ import annotations

import hashlib
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
MEMORY_KINDS = {"preference", "fact", "context", "low_confidence", "note"}
DEFAULT_CONFIDENCE = 0.5
PREFERENCE_FALLBACK_HINTS = (
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
STABLE_FACT_FALLBACK_HINTS = (
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
CONTEXT_FALLBACK_HINTS = (
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
class NoteClassification:
    section: str
    kind: str
    confidence: float
    ttl_days: int | None


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


@dataclass(slots=True)
class ConsolidationState:
    last_consolidated_log: str = ""
    log_hashes: dict[str, str] | None = None
    merge_summary_hashes: dict[str, str] | None = None
    merge_summary_files: dict[str, str] | None = None
    summary_file_hashes: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.log_hashes is None:
            self.log_hashes = {}
        if self.merge_summary_hashes is None:
            self.merge_summary_hashes = {}
        if self.merge_summary_files is None:
            self.merge_summary_files = {}
        if self.summary_file_hashes is None:
            self.summary_file_hashes = {}


@dataclass(slots=True)
class GeneratedSummaryUpdate:
    log_date: str
    content_hash: str
    summary_block: str
    parsed: ParsedSummary | None


@dataclass(slots=True)
class IncrementalMergePlan:
    delta_summaries: list[ParsedSummary]
    changed_summary_dates: set[str]
    rebuild_summaries: list[ParsedSummary]
    summary_hashes: dict[str, str]
    summary_files: dict[str, str]
    summary_file_hashes: dict[str, str]
    requires_rebuild: bool = False


def consolidate_workspace_memory(workspace: Path, config: AppConfig | None = None) -> None:
    generate_workspace_summaries(workspace, config=config)
    merge_workspace_memory(workspace, config=config)


def generate_workspace_summaries(workspace: Path, config: AppConfig | None = None) -> None:
    logs_dir = workspace / "logs"
    if not logs_dir.exists():
        return

    summaries_dir = workspace / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_file = summaries_dir / f"{date.today().isoformat()}.md"
    existing_summary_blocks = _load_existing_summary_blocks(summary_file)
    existing_valid_summaries = {
        log_date: _parse_summary(log_date, block)
        for log_date, block in existing_summary_blocks.items()
    }
    existing_valid_summaries = {
        log_date: parsed
        for log_date, parsed in existing_valid_summaries.items()
        if parsed is not None
    }
    state = _load_consolidation_state(workspace)
    summary_map = dict(existing_summary_blocks)
    log_files = sorted(logs_dir.glob("*.md"))

    generated_updates, last_processed_log = _generate_summary_updates(
        workspace=workspace,
        log_files=log_files,
        existing_valid_summaries=existing_valid_summaries,
        state=state,
        config=config,
    )
    _apply_summary_updates(summary_file, summary_map, generated_updates)

    if last_processed_log:
        state.last_consolidated_log = last_processed_log
    _write_consolidation_state(workspace, state)


def merge_workspace_memory(workspace: Path, config: AppConfig | None = None) -> None:
    state = _load_consolidation_state(workspace)
    merge_plan = _plan_incremental_memory_merge(workspace, state)

    if merge_plan.requires_rebuild:
        _merge_parsed_summaries_into_memory(
            workspace,
            merge_plan.rebuild_summaries,
            config=config,
            replace_summary_sources=True,
        )
    elif merge_plan.delta_summaries:
        _merge_parsed_summaries_into_memory(
            workspace,
            merge_plan.delta_summaries,
            config=config,
            replace_summary_sources_for_dates=merge_plan.changed_summary_dates,
        )

    state.merge_summary_hashes = merge_plan.summary_hashes
    state.merge_summary_files = merge_plan.summary_files
    state.summary_file_hashes = merge_plan.summary_file_hashes
    _write_consolidation_state(workspace, state)


def _generate_summary_updates(
    *,
    workspace: Path,
    log_files: list[Path],
    existing_valid_summaries: dict[str, ParsedSummary],
    state: ConsolidationState,
    config: AppConfig | None,
) -> tuple[list[GeneratedSummaryUpdate], str]:
    updates: list[GeneratedSummaryUpdate] = []
    last_processed_log = state.last_consolidated_log

    for log_file in log_files:
        content = log_file.read_text(encoding="utf-8").strip()
        if not content:
            continue

        log_date = log_file.stem
        content_hash = _content_hash(content)
        if not _should_process_log(log_date, content_hash, state):
            if log_date > last_processed_log:
                last_processed_log = log_date
            continue

        generation = _generate_semantic_summary(
            log_date=log_date,
            log_content=content,
            workspace=workspace,
            config=config,
        )
        parsed = _parse_summary(log_date, generation.text)
        if parsed is None:
            preserved = existing_valid_summaries.get(log_date)
            summary_block = preserved.to_markdown() if preserved is not None else _fallback_summary(
                log_date,
                content,
                reason="invalid summary structure",
            )
            updates.append(
                GeneratedSummaryUpdate(
                    log_date=log_date,
                    content_hash=content_hash,
                    summary_block=summary_block,
                    parsed=None,
                )
            )
        elif generation.is_fallback:
            preserved = existing_valid_summaries.get(log_date)
            updates.append(
                GeneratedSummaryUpdate(
                    log_date=log_date,
                    content_hash=content_hash,
                    summary_block=preserved.to_markdown() if preserved is not None else generation.text,
                    parsed=None,
                )
            )
        else:
            updates.append(
                GeneratedSummaryUpdate(
                    log_date=log_date,
                    content_hash=content_hash,
                    summary_block=parsed.to_markdown(),
                    parsed=parsed,
                )
            )

        state.log_hashes[log_date] = content_hash
        if log_date > last_processed_log:
            last_processed_log = log_date

    return updates, last_processed_log


def _apply_summary_updates(
    summary_file: Path,
    summary_map: dict[str, str],
    updates: list[GeneratedSummaryUpdate],
) -> None:
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    for update in updates:
        _upsert_summary_block(summary_file, update.log_date, update.summary_block, summary_map)


def _merge_generated_notes_into_memory(
    workspace: Path,
    updates: list[GeneratedSummaryUpdate],
    *,
    config: AppConfig | None,
) -> None:
    parsed_summaries = [update.parsed for update in updates if update.parsed is not None]
    _merge_parsed_summaries_into_memory(workspace, parsed_summaries, config=config)


def _merge_parsed_summaries_into_memory(
    workspace: Path,
    summaries: list[ParsedSummary],
    *,
    config: AppConfig | None,
    replace_summary_sources: bool = False,
    replace_summary_sources_for_dates: set[str] | None = None,
) -> None:
    memory_file = workspace / "MEMORY.md"
    memory_sections = _read_memory_sections(memory_file)
    metadata_updates = {name: {} for name in MEMORY_SECTIONS}
    existing_metadata = _load_memory_metadata(memory_file)

    if replace_summary_sources or replace_summary_sources_for_dates:
        target_sources = {
            f"summary:{_normalize_item(log_date)}"
            for log_date in (replace_summary_sources_for_dates or set())
            if _normalize_item(log_date)
        }
        filtered_sections: dict[str, list[str]] = {}
        for section_name in MEMORY_SECTIONS:
            retained_entries = [
                entry
                for entry in existing_metadata.get(section_name, [])
                if not (
                    _normalize_item(entry.get("source", "")).startswith("summary:")
                    if replace_summary_sources
                    else _normalize_item(entry.get("source", "")) in target_sources
                )
            ]
            existing_metadata[section_name] = retained_entries
            filtered_sections[section_name] = [entry["content"] for entry in retained_entries]
        memory_sections.update(filtered_sections)

    section_seen = {
        name: {_normalize_item(item) for item in memory_sections.get(name, [])}
        for name in MEMORY_SECTIONS
    }

    for summary in summaries:
        for candidate in summary.potential_long_term_notes[:MAX_LONG_TERM_NOTES]:
            normalized = _normalize_item(candidate)
            if not normalized:
                continue
            classification = _classify_note_semantics(
                normalized,
                source=f"summary:{summary.log_date}",
                config=config,
                workspace=workspace,
            )
            target_section = classification.section
            if normalized in section_seen[target_section]:
                continue
            memory_sections[target_section].append(normalized)
            section_seen[target_section].add(normalized)
            metadata_updates[target_section][normalized] = _build_note_metadata(
                normalized,
                classification=classification,
                source=f"summary:{summary.log_date}",
            )

    if not (memory_file.exists() or any(items for items in memory_sections.values())):
        return
    if not memory_file.exists():
        memory_file.write_text("# Memory\n\n## User Preferences\n\n## Stable Facts\n\n## Saved Notes\n", encoding="utf-8")
    _rewrite_memory_sections(
        memory_file,
        memory_sections,
        config=config,
        workspace=workspace,
        metadata_updates=_merge_existing_metadata_updates(existing_metadata, metadata_updates),
    )


def _merge_existing_metadata_updates(
    existing_metadata: dict[str, list[dict[str, str]]],
    metadata_updates: dict[str, dict[str, dict[str, str]]],
) -> dict[str, dict[str, dict[str, str]]]:
    merged_updates: dict[str, dict[str, dict[str, str]]] = {
        section_name: dict(section_updates)
        for section_name, section_updates in metadata_updates.items()
    }
    for section_name in MEMORY_SECTIONS:
        section_updates = merged_updates.setdefault(section_name, {})
        for entry in existing_metadata.get(section_name, []):
            content = _normalize_item(entry.get("content", ""))
            if not content or content in section_updates:
                continue
            section_updates[content] = dict(entry)
    return merged_updates


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


def _classify_long_term_note(
    note: str,
    *,
    source: str = "unknown",
    config: AppConfig | None = None,
    workspace: Path | None = None,
) -> NoteClassification:
    return _classify_note_semantics(note, source=source, config=config, workspace=workspace)


def _build_note_metadata(
    note: str,
    *,
    classification: NoteClassification | None = None,
    section_name: str | None = None,
    source: str,
    created_at: str | None = None,
) -> dict[str, str]:
    normalized = _normalize_item(note)
    normalized_source = _normalize_item(source) or "unknown"
    resolved_classification = classification or _classify_note_semantics(
        normalized,
        source=normalized_source,
        fallback_section=section_name,
    )
    return {
        "created_at": created_at or _now_isoformat(),
        "source": normalized_source,
        "section": resolved_classification.section,
        "kind": resolved_classification.kind,
        "confidence": _format_confidence(resolved_classification.confidence),
        "ttl_days": "" if resolved_classification.ttl_days is None else str(resolved_classification.ttl_days),
    }


def _classify_memory_kind(
    note: str,
    *,
    section_name: str,
    source: str,
    config: AppConfig | None = None,
    workspace: Path | None = None,
) -> str:
    return _classify_note_semantics(
        note,
        source=source,
        fallback_section=section_name,
        config=config,
        workspace=workspace,
    ).kind


def _classify_note_semantics(
    note: str,
    *,
    source: str,
    fallback_section: str | None = None,
    config: AppConfig | None = None,
    workspace: Path | None = None,
) -> NoteClassification:
    normalized = _normalize_item(note)
    if not normalized:
        return NoteClassification(
            section=fallback_section if fallback_section in MEMORY_SECTIONS else "Saved Notes",
            kind="low_confidence",
            confidence=0.0,
            ttl_days=None,
        )

    decision = _semantic_note_classification_decision(normalized, source=source, config=config, workspace=workspace)
    if decision is not None:
        return decision
    return _fallback_note_classification(normalized, source=source, fallback_section=fallback_section)


def _semantic_note_classification_decision(
    note: str,
    *,
    source: str,
    config: AppConfig | None,
    workspace: Path | None,
) -> NoteClassification | None:
    if config is None or workspace is None:
        return None

    cli_path = config.selected_cli_path.strip()
    if not cli_path or shutil.which(cli_path) is None:
        return None

    prompt = _build_note_classification_prompt(note, source)
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

    return _parse_note_classification(text, source=source)


def _build_note_classification_prompt(note: str, source: str) -> str:
    note_json = json.dumps(note, ensure_ascii=False)
    source_json = json.dumps(_normalize_item(source) or "unknown", ensure_ascii=False)
    return f"""You are classifying a candidate long-term memory note for workspace memory.

Return JSON only with this schema:
{{
  \"section\": \"User Preferences\" | \"Stable Facts\" | \"Saved Notes\",
  \"kind\": \"preference\" | \"fact\" | \"context\" | \"low_confidence\" | \"note\",
  \"confidence\": <number between 0 and 1>,
  \"ttl_days\": <integer or null>
}}

Classification rules:
- User Preferences: durable user preferences, styles, dislikes, or recurring ways of working.
- Stable Facts: durable facts, constraints, architecture facts, or project truths likely to remain useful.
- Saved Notes: ambiguous, weaker, or general notes that are still worth saving.
- kind=context only for time-bound or temporary context that should expire.
- kind=preference must use section User Preferences.
- kind=fact must use section Stable Facts.
- kind=context, kind=low_confidence, and kind=note must use section Saved Notes.
- Use ttl_days=7 for short-lived context. Otherwise use null.
- Use low_confidence when the statement might be useful but wording is ambiguous or weakly grounded.
- Prefer semantic meaning over keyword matches.
- Be conservative: if unsure, choose Saved Notes with kind=low_confidence.

Source:
{source_json}

Note:
{note_json}
"""


def _parse_note_classification(text: str, *, source: str) -> NoteClassification | None:
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

    section = _normalize_item(str(payload.get("section", "")))
    kind = _normalize_item(str(payload.get("kind", ""))).lower()
    confidence = _coerce_confidence(payload.get("confidence"))
    ttl_days = _coerce_ttl_days(payload.get("ttl_days"))

    if section not in MEMORY_SECTIONS or kind not in MEMORY_KINDS or confidence is None:
        return None
    if kind == "preference" and section != "User Preferences":
        return None
    if kind == "fact" and section != "Stable Facts":
        return None
    if kind in {"context", "low_confidence", "note"} and section != "Saved Notes":
        return None
    if kind == "context" and ttl_days is None:
        ttl_days = CONTEXT_NOTE_TTL_DAYS
    if kind != "context" and ttl_days is not None:
        ttl_days = None

    if source.startswith("summary:") and kind == "note" and confidence < 0.6:
        kind = "low_confidence"

    return NoteClassification(
        section=section,
        kind=kind,
        confidence=confidence,
        ttl_days=ttl_days,
    )


def _fallback_note_classification(note: str, *, source: str, fallback_section: str | None) -> NoteClassification:
    lowered = _normalize_item(note).lower()
    if _is_context_note(lowered):
        return NoteClassification(
            section="Saved Notes",
            kind="context",
            confidence=0.65,
            ttl_days=CONTEXT_NOTE_TTL_DAYS,
        )
    if any(hint in lowered for hint in PREFERENCE_FALLBACK_HINTS):
        return NoteClassification(
            section="User Preferences",
            kind="preference",
            confidence=0.7,
            ttl_days=None,
        )
    if any(hint in lowered for hint in STABLE_FACT_FALLBACK_HINTS):
        return NoteClassification(
            section="Stable Facts",
            kind="fact",
            confidence=0.68,
            ttl_days=None,
        )

    if source.startswith("summary:"):
        return NoteClassification(
            section="Saved Notes",
            kind="low_confidence",
            confidence=0.45,
            ttl_days=None,
        )

    if fallback_section == "User Preferences":
        return NoteClassification(section="User Preferences", kind="preference", confidence=0.55, ttl_days=None)
    if fallback_section == "Stable Facts":
        return NoteClassification(section="Stable Facts", kind="fact", confidence=0.55, ttl_days=None)
    return NoteClassification(section="Saved Notes", kind="note", confidence=DEFAULT_CONFIDENCE, ttl_days=None)


def _is_context_note(note: str) -> bool:
    lowered = _normalize_item(note).lower()
    return any(hint in lowered for hint in CONTEXT_FALLBACK_HINTS)


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


def _load_all_valid_summaries(summaries_dir: Path) -> list[ParsedSummary]:
    if not summaries_dir.exists():
        return []

    summaries: list[ParsedSummary] = []
    for summary_file in sorted(summaries_dir.glob("*.md")):
        for log_date, block in _load_existing_summary_blocks(summary_file).items():
            parsed = _parse_summary(log_date, block)
            if parsed is not None:
                summaries.append(parsed)
    return summaries


def _plan_incremental_memory_merge(workspace: Path, state: ConsolidationState) -> IncrementalMergePlan:
    summaries_dir = workspace / "summaries"
    current_summaries: list[ParsedSummary] = []
    current_hashes: dict[str, str] = {}
    current_files: dict[str, str] = {}
    current_file_hashes: dict[str, str] = {}

    if summaries_dir.exists():
        for summary_file in sorted(summaries_dir.glob("*.md")):
            file_hash = _content_hash(summary_file.read_text(encoding="utf-8"))
            relative_name = summary_file.relative_to(workspace).as_posix()
            current_file_hashes[relative_name] = file_hash
            for log_date, block in _load_existing_summary_blocks(summary_file).items():
                parsed = _parse_summary(log_date, block)
                if parsed is None:
                    continue
                current_summaries.append(parsed)
                current_hashes[log_date] = _content_hash(parsed.to_markdown())
                current_files[log_date] = relative_name

    previous_hashes = state.merge_summary_hashes or {}
    previous_files = state.merge_summary_files or {}
    previous_file_hashes = state.summary_file_hashes or {}
    changed_dates = {
        log_date
        for log_date, content_hash in current_hashes.items()
        if previous_hashes.get(log_date) != content_hash or previous_files.get(log_date) != current_files.get(log_date)
    }
    removed_dates = set(previous_hashes) - set(current_hashes)
    changed_files = {
        relative_name
        for relative_name, file_hash in current_file_hashes.items()
        if previous_file_hashes.get(relative_name) != file_hash
    }
    removed_files = set(previous_file_hashes) - set(current_file_hashes)
    requires_rebuild = bool(removed_dates or removed_files)

    summaries_by_date = {summary.log_date: summary for summary in current_summaries}
    delta_summaries = [summaries_by_date[log_date] for log_date in sorted(changed_dates) if log_date in summaries_by_date]
    rebuild_summaries = current_summaries if requires_rebuild else []
    return IncrementalMergePlan(
        delta_summaries=delta_summaries,
        changed_summary_dates=set(changed_dates),
        rebuild_summaries=rebuild_summaries,
        summary_hashes=current_hashes,
        summary_files=current_files,
        summary_file_hashes=current_file_hashes,
        requires_rebuild=requires_rebuild,
    )


def _load_existing_valid_summaries(summary_file: Path) -> dict[str, ParsedSummary]:
    if not summary_file.exists():
        return {}

    summaries: dict[str, ParsedSummary] = {}
    for log_date, block in _load_existing_summary_blocks(summary_file).items():
        parsed = _parse_summary(log_date, block)
        if parsed is not None:
            summaries[log_date] = parsed
    return summaries


def _load_existing_summary_blocks(summary_file: Path) -> dict[str, str]:
    if not summary_file.exists():
        return {}

    summaries: dict[str, str] = {}
    for block in _split_summary_blocks(summary_file.read_text(encoding="utf-8")):
        log_date = _extract_log_date(block)
        if not log_date:
            continue
        summaries[log_date] = block.strip()
    return summaries


def _serialize_summary_map(summary_map: dict[str, str]) -> dict[str, str]:
    return {
        log_date: summary_map[log_date].strip()
        for log_date in sorted(summary_map)
        if _normalize_item(summary_map[log_date])
    }


def _write_summary_map(summary_file: Path, summary_map: dict[str, str]) -> None:
    serialized = _serialize_summary_map(summary_map)
    if not serialized:
        return
    summary_file.write_text("\n\n".join(serialized[log_date] for log_date in serialized).rstrip() + "\n", encoding="utf-8")


def _upsert_summary_block(summary_file: Path, log_date: str, block: str, summary_map: dict[str, str]) -> None:
    normalized_block = block.strip()
    if not normalized_block:
        return
    existing_block = _normalize_item(summary_map.get(log_date, ""))
    if existing_block == _normalize_item(normalized_block):
        return

    if log_date not in summary_map and _can_append_summary_block(summary_file, log_date, summary_map):
        with summary_file.open("a", encoding="utf-8") as handle:
            if summary_file.stat().st_size > 0:
                handle.write("\n\n")
            handle.write(normalized_block.rstrip() + "\n")
        summary_map[log_date] = normalized_block
        return

    summary_map[log_date] = normalized_block
    _write_summary_map(summary_file, summary_map)


def _can_append_summary_block(summary_file: Path, log_date: str, summary_map: dict[str, str]) -> bool:
    if not summary_file.exists():
        return True
    if not summary_map:
        return True
    return log_date > max(summary_map)


def _consolidation_state_file(workspace: Path) -> Path:
    return workspace / "summaries" / "consolidation_state.json"


def _load_consolidation_state(workspace: Path) -> ConsolidationState:
    state_file = _consolidation_state_file(workspace)
    if not state_file.exists():
        return ConsolidationState()

    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ConsolidationState()
    if not isinstance(payload, dict):
        return ConsolidationState()

    last_consolidated_log = _normalize_item(str(payload.get("last_consolidated_log", "")))
    raw_log_hashes = payload.get("log_hashes", {})
    log_hashes: dict[str, str] = {}
    if isinstance(raw_log_hashes, dict):
        for log_date, content_hash in raw_log_hashes.items():
            normalized_log_date = _normalize_item(str(log_date))
            normalized_hash = _normalize_item(str(content_hash))
            if normalized_log_date and normalized_hash:
                log_hashes[normalized_log_date] = normalized_hash

    def _load_string_map(key: str) -> dict[str, str]:
        raw = payload.get(key, {})
        loaded: dict[str, str] = {}
        if not isinstance(raw, dict):
            return loaded
        for map_key, map_value in raw.items():
            normalized_key = _normalize_item(str(map_key))
            normalized_value = _normalize_item(str(map_value))
            if normalized_key and normalized_value:
                loaded[normalized_key] = normalized_value
        return loaded

    return ConsolidationState(
        last_consolidated_log=last_consolidated_log,
        log_hashes=log_hashes,
        merge_summary_hashes=_load_string_map("merge_summary_hashes"),
        merge_summary_files=_load_string_map("merge_summary_files"),
        summary_file_hashes=_load_string_map("summary_file_hashes"),
    )


def _write_consolidation_state(workspace: Path, state: ConsolidationState) -> None:
    state_file = _consolidation_state_file(workspace)
    payload = {
        "version": 2,
        "last_consolidated_log": state.last_consolidated_log,
        "log_hashes": {
            log_date: content_hash
            for log_date, content_hash in sorted((state.log_hashes or {}).items())
            if log_date and content_hash
        },
        "merge_summary_hashes": {
            log_date: content_hash
            for log_date, content_hash in sorted((state.merge_summary_hashes or {}).items())
            if log_date and content_hash
        },
        "merge_summary_files": {
            log_date: relative_name
            for log_date, relative_name in sorted((state.merge_summary_files or {}).items())
            if log_date and relative_name
        },
        "summary_file_hashes": {
            relative_name: content_hash
            for relative_name, content_hash in sorted((state.summary_file_hashes or {}).items())
            if relative_name and content_hash
        },
    }
    state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _should_process_log(log_date: str, content_hash: str, state: ConsolidationState) -> bool:
    previous_hash = (state.log_hashes or {}).get(log_date)
    if previous_hash != content_hash:
        return True
    return log_date > state.last_consolidated_log


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
    section = _normalize_item((metadata or {}).get("section", "")) or section_name
    kind = _normalize_item((metadata or {}).get("kind", ""))
    confidence = _normalize_confidence((metadata or {}).get("confidence", ""))
    ttl_days = _normalize_ttl_days((metadata or {}).get("ttl_days", ""))

    if section not in MEMORY_SECTIONS or kind not in MEMORY_KINDS or confidence is None:
        classification = _classify_note_semantics(
            normalized_content,
            source=raw_source,
            fallback_section=section_name,
        )
        section = classification.section
        kind = classification.kind
        confidence = classification.confidence
        ttl_days = classification.ttl_days

    if section != section_name:
        section = section_name
    if kind == "context" and ttl_days is None:
        ttl_days = CONTEXT_NOTE_TTL_DAYS
    if kind != "context":
        ttl_days = None

    return {
        "content": normalized_content,
        "created_at": created_at,
        "source": raw_source,
        "section": section,
        "kind": kind,
        "confidence": _format_confidence(confidence),
        "ttl_days": "" if ttl_days is None else str(ttl_days),
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
    ttl_days = _normalize_ttl_days(entry.get("ttl_days", "")) or CONTEXT_NOTE_TTL_DAYS
    if created_at is None:
        return False
    return created_at < datetime.now(timezone.utc) - timedelta(days=ttl_days)


def _entry_sort_key(entry: dict[str, str]) -> datetime:
    return _parse_timestamp(entry.get("created_at", "")) or datetime.fromtimestamp(0, tz=timezone.utc)


def _merge_note_entries(existing: dict[str, str], candidate: dict[str, str]) -> dict[str, str]:
    merged = dict(existing)
    if _entry_sort_key(candidate) >= _entry_sort_key(existing):
        merged["created_at"] = candidate["created_at"]
        if candidate.get("source") and candidate["source"] != "legacy":
            merged["source"] = candidate["source"]
    if merged.get("kind") in {"legacy", "note", "low_confidence"} and candidate.get("kind"):
        merged["kind"] = candidate["kind"]
    merged_confidence = _normalize_confidence(merged.get("confidence", ""))
    candidate_confidence = _normalize_confidence(candidate.get("confidence", ""))
    if candidate_confidence is not None and (merged_confidence is None or candidate_confidence >= merged_confidence):
        merged["confidence"] = _format_confidence(candidate_confidence)
        merged["section"] = candidate.get("section", merged.get("section", "Saved Notes"))
        merged["ttl_days"] = candidate.get("ttl_days", merged.get("ttl_days", ""))
    elif merged_confidence is not None:
        merged["confidence"] = _format_confidence(merged_confidence)
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
                    "section": _normalize_item(str(raw_entry.get("section", ""))),
                    "kind": _normalize_item(str(raw_entry.get("kind", ""))),
                    "confidence": _normalize_item(str(raw_entry.get("confidence", ""))),
                    "ttl_days": _normalize_item(str(raw_entry.get("ttl_days", ""))),
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
                    "section": entry.get("section", section_name),
                    "kind": entry["kind"],
                    "confidence": entry.get("confidence", _format_confidence(DEFAULT_CONFIDENCE)),
                    "ttl_days": entry.get("ttl_days", ""),
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


def _coerce_confidence(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
    elif isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            numeric = float(cleaned)
        except ValueError:
            return None
    else:
        return None
    if numeric < 0 or numeric > 1:
        return None
    return numeric


def _normalize_confidence(value: object) -> float | None:
    return _coerce_confidence(value)


def _format_confidence(value: float) -> str:
    bounded = min(max(value, 0.0), 1.0)
    return f"{bounded:.3f}".rstrip("0").rstrip(".") or "0"


def _coerce_ttl_days(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        ttl_days = value
    elif isinstance(value, float):
        if not value.is_integer():
            return None
        ttl_days = int(value)
    elif isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            ttl_days = int(cleaned)
        except ValueError:
            return None
    else:
        return None
    if ttl_days <= 0:
        return None
    return ttl_days


def _normalize_ttl_days(value: object) -> int | None:
    return _coerce_ttl_days(value)


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
