from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

from app.config import AppConfig

SUMMARY_FALLBACK_PREFIX = "Semantic summary unavailable"


def consolidate_workspace_memory(workspace: Path, config: AppConfig | None = None) -> None:
    logs_dir = workspace / "logs"
    if not logs_dir.exists():
        return

    summaries_dir = workspace / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)

    summary_sections: list[str] = []
    saved_notes = _read_memory_section_items(workspace / "MEMORY.md", "Saved Notes")
    note_seen = {_normalize_item(item) for item in saved_notes}

    for log_file in sorted(logs_dir.glob("*.md")):
        content = log_file.read_text(encoding="utf-8").strip()
        if not content:
            continue

        semantic_summary = _generate_semantic_summary(
            log_date=log_file.stem,
            log_content=content,
            workspace=workspace,
            config=config,
        )
        summary_sections.append(semantic_summary)

        for candidate in _extract_memory_candidates_from_summary(semantic_summary):
            normalized = _normalize_item(candidate)
            if normalized and normalized not in note_seen:
                saved_notes.append(candidate)
                note_seen.add(normalized)

    if summary_sections:
        summary_file = summaries_dir / f"{date.today().isoformat()}.md"
        summary_file.write_text("\n\n".join(summary_sections).rstrip() + "\n", encoding="utf-8")

    _rewrite_saved_notes(workspace / "MEMORY.md", saved_notes)


def _generate_semantic_summary(log_date: str, log_content: str, workspace: Path, config: AppConfig | None) -> str:
    if config is None:
        return _fallback_summary(log_date, log_content, reason="missing runtime config")

    cli_path = config.selected_cli_path.strip()
    if not cli_path or shutil.which(cli_path) is None:
        return _fallback_summary(log_date, log_content, reason=f"{config.ai_provider} CLI not available")

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
        return _fallback_summary(log_date, log_content, reason=str(exc))

    text = _extract_cli_text(config.ai_provider, completed.stdout.strip(), completed.stderr.strip())
    if completed.returncode != 0 or not text:
        error_text = text or completed.stderr.strip() or f"{config.ai_provider} CLI exited with status {completed.returncode}"
        return _fallback_summary(log_date, log_content, reason=error_text)

    normalized = text.strip()
    if not normalized.startswith(f"## {log_date}"):
        normalized = f"## {log_date}\n{normalized}"
    return normalized


def _build_summary_prompt(log_date: str, log_content: str) -> str:
    return f"""You are summarizing a single conversation log for long-term workspace memory.

Return markdown only. Use exactly this structure:
## {log_date}
### Semantic Summary
- <3-5 bullets capturing the user's goals, durable context, and outcomes>
### Potential Long-Term Notes
- <0-5 bullets for stable preferences, facts, or constraints worth saving>

Rules:
- Focus on durable information, not every turn.
- Do not repeat raw Q/A transcripts.
- Do not invent facts.
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


def _extract_memory_candidates_from_summary(summary: str) -> list[str]:
    candidates: list[str] = []
    in_notes_section = False

    for raw_line in summary.splitlines():
        stripped = raw_line.strip()
        if stripped == "### Potential Long-Term Notes":
            in_notes_section = True
            continue
        if stripped.startswith("### ") and stripped != "### Potential Long-Term Notes":
            in_notes_section = False
            continue
        if not in_notes_section or not stripped.startswith("- "):
            continue

        item = _normalize_item(stripped[2:])
        if item and item.lower() != "none" and not item.lower().startswith(SUMMARY_FALLBACK_PREFIX.lower()):
            candidates.append(item)
    return candidates


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


def _read_memory_section_items(memory_file: Path, section_name: str) -> list[str]:
    if not memory_file.exists():
        return []

    items: list[str] = []
    current_section = ""
    for raw_line in memory_file.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            current_section = stripped.removeprefix("## ").strip()
            continue
        if current_section == section_name and stripped.startswith("- "):
            item = _normalize_item(stripped[2:])
            if item:
                items.append(item)
    return items


def _rewrite_saved_notes(memory_file: Path, saved_notes: list[str]) -> None:
    if not memory_file.exists():
        return

    lines = memory_file.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    current_section = ""
    skipping_saved_notes = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("## "):
            if skipping_saved_notes:
                output.extend(f"- {item}" for item in _dedupe(saved_notes))
                output.append("")
                skipping_saved_notes = False
            current_section = stripped.removeprefix("## ").strip()
            output.append(raw_line)
            if current_section == "Saved Notes":
                skipping_saved_notes = True
            continue
        if skipping_saved_notes and stripped.startswith("- "):
            continue
        output.append(raw_line)

    if skipping_saved_notes:
        output.extend(f"- {item}" for item in _dedupe(saved_notes))
        output.append("")

    memory_file.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


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
