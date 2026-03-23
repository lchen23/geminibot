#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agent.engine import AgentRequest, GeminiAgentEngine
from app.config import AppConfig
from app.memory.store import MemoryStore
from app.scheduler.store import SchedulerStore


DEFAULT_MEMORY_PROMPT = "记住我喜欢简洁回复"
DEFAULT_SCHEDULER_PROMPT = "明天下午三点提醒我开会"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-run natural-language acceptance for memory and scheduler tool bridge."
    )
    parser.add_argument(
        "--conversation-id",
        default="tool-bridge-acceptance",
        help="Conversation ID used for the acceptance workspace.",
    )
    parser.add_argument(
        "--chat-id",
        default="acceptance-chat",
        help="Chat ID injected into the agent environment.",
    )
    parser.add_argument(
        "--user-id",
        default="acceptance-user",
        help="User ID injected into the agent environment.",
    )
    parser.add_argument(
        "--memory-prompt",
        default=DEFAULT_MEMORY_PROMPT,
        help="Natural-language prompt for memory acceptance.",
    )
    parser.add_argument(
        "--scheduler-prompt",
        default=DEFAULT_SCHEDULER_PROMPT,
        help="Natural-language prompt for scheduler acceptance.",
    )
    parser.add_argument(
        "--only",
        choices=["all", "memory", "scheduler"],
        default="all",
        help="Choose which acceptance case to run.",
    )
    parser.add_argument(
        "--print-raw-output",
        action="store_true",
        help="Include raw model output in stdout summary.",
    )
    parser.add_argument(
        "--notes-file",
        default="notes/tool-bridge-acceptance.md",
        help="Markdown file used to append acceptance run notes.",
    )
    parser.add_argument(
        "--skip-notes",
        action="store_true",
        help="Do not append a Markdown note for this run.",
    )
    return parser.parse_args()


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot_state(config: AppConfig, workspace: Path, chat_id: str) -> dict[str, Any]:
    audit_path = workspace / "tool_audit.jsonl"
    memory_path = workspace / "MEMORY.md"
    schedules_path = config.data_root / "schedules.json"
    scheduler_store = SchedulerStore(config)

    audit_text = read_text(audit_path)
    schedule_tasks = scheduler_store.list_tasks(chat_id=chat_id)
    return {
        "audit_exists": audit_path.exists(),
        "audit_line_count": len(audit_text.splitlines()) if audit_text else 0,
        "memory_text": read_text(memory_path),
        "schedule_tasks": schedule_tasks,
        "schedule_count": len(schedule_tasks),
        "schedules_json": read_json(schedules_path, []),
    }


def run_case(
    *,
    engine: GeminiAgentEngine,
    conversation_id: str,
    chat_id: str,
    user_id: str,
    prompt: str,
    source: str,
) -> dict[str, Any]:
    result = engine.run(
        AgentRequest(
            conversation_id=conversation_id,
            chat_id=chat_id,
            user_id=user_id,
            text=prompt,
            source=source,
        )
    )
    return {
        "text": result.text,
        "raw_output": result.raw_output,
        "session_id": result.session_id,
        "model": result.model,
    }


def build_change_summary(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "audit_changed": before["audit_line_count"] != after["audit_line_count"],
        "memory_changed": before["memory_text"] != after["memory_text"],
        "schedule_count_changed": before["schedule_count"] != after["schedule_count"],
        "schedules_json_changed": before["schedules_json"] != after["schedules_json"],
    }


def append_notes(*, notes_path: Path, summary: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> None:
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds")
    lines = [
        f"## {timestamp}",
        "",
        f"- CLI: `{summary['cli_path']}`",
        f"- Conversation: `{summary['conversation_id']}`",
        f"- Chat: `{summary['chat_id']}`",
        f"- Workspace: `{summary['workspace']}`",
        "",
        "### Runs",
    ]

    for name, run in summary["runs"].items():
        lines.extend(
            [
                f"- **{name}**",
                f"  - session_id: `{run.get('session_id')}`",
                f"  - model: `{run.get('model')}`",
                f"  - text: {run.get('text')}",
            ]
        )

    changes = summary["changes"]
    lines.extend(
        [
            "",
            "### Artifact Changes",
            f"- audit_changed: `{changes['audit_changed']}` ({before['audit_line_count']} -> {after['audit_line_count']})",
            f"- memory_changed: `{changes['memory_changed']}`",
            f"- schedule_count_changed: `{changes['schedule_count_changed']}` ({before['schedule_count']} -> {after['schedule_count']})",
            f"- schedules_json_changed: `{changes['schedules_json_changed']}`",
            "",
            "### Artifacts",
            f"- report: `{summary['artifacts']['report_file']}`",
            f"- audit_log: `{summary['artifacts']['audit_log']}`",
            f"- memory_file: `{summary['artifacts']['memory_file']}`",
            f"- schedules_json: `{summary['artifacts']['schedules_json']}`",
            "",
        ]
    )

    with notes_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def main() -> None:
    args = parse_args()
    config = AppConfig.load()
    memory_store = MemoryStore(config)
    engine = GeminiAgentEngine(config=config, memory_store=memory_store)
    workspace = engine.workspace_manager.ensure_workspace(args.conversation_id)

    before = snapshot_state(config, workspace, args.chat_id)
    runs: dict[str, Any] = {}

    if args.only in {"all", "memory"}:
        runs["memory"] = run_case(
            engine=engine,
            conversation_id=args.conversation_id,
            chat_id=args.chat_id,
            user_id=args.user_id,
            prompt=args.memory_prompt,
            source="acceptance-memory",
        )

    if args.only in {"all", "scheduler"}:
        runs["scheduler"] = run_case(
            engine=engine,
            conversation_id=args.conversation_id,
            chat_id=args.chat_id,
            user_id=args.user_id,
            prompt=args.scheduler_prompt,
            source="acceptance-scheduler",
        )

    after = snapshot_state(config, workspace, args.chat_id)
    notes_path = Path(args.notes_file)
    if not notes_path.is_absolute():
        notes_path = Path(__file__).resolve().parent / notes_path

    summary = {
        "cli_path": config.gemini_cli_path,
        "conversation_id": args.conversation_id,
        "chat_id": args.chat_id,
        "workspace": str(workspace),
        "runs": runs,
        "changes": build_change_summary(before, after),
        "artifacts": {
            "memory_file": str(workspace / "MEMORY.md"),
            "audit_log": str(workspace / "tool_audit.jsonl"),
            "schedules_json": str(config.data_root / "schedules.json"),
            "sessions_json": str(config.data_root / "sessions.json"),
            "report_file": str(workspace / "acceptance_report.json"),
            "notes_file": str(notes_path),
        },
    }

    report = dict(summary)
    report["before"] = before
    report["after"] = after
    (workspace / "acceptance_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if not args.skip_notes:
        append_notes(notes_path=notes_path, summary=summary, before=before, after=after)

    if not args.print_raw_output:
        for case in summary["runs"].values():
            case.pop("raw_output", None)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
