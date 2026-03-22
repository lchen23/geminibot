from __future__ import annotations

import shutil
from pathlib import Path
from textwrap import dedent

from app.config import AppConfig


TEMPLATE_FILES = ["SOUL.md", "IDENTITY.md", "USER.md", "AGENT.md", "MEMORY.md"]

TOOL_BRIDGE_SCRIPT = dedent(
    """\
    #!/usr/bin/env python3
    from __future__ import annotations

    import argparse
    import json
    import os
    import sys
    from datetime import datetime, timezone
    from pathlib import Path


    def _project_root() -> Path:
        configured = os.getenv("GEMINIBOT_PROJECT_ROOT")
        if configured:
            return Path(configured)
        return Path(__file__).resolve().parents[3]


    sys.path.insert(0, str(_project_root()))

    from app.config import AppConfig
    from app.memory.tools import MemoryTools
    from app.scheduler.tools import SchedulerTools


    def _workspace() -> Path:
        configured = os.getenv("GEMINIBOT_WORKSPACE")
        if configured:
            return Path(configured)
        return Path(__file__).resolve().parents[1]


    def _context(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            raise ValueError(f"Missing required environment variable: {name}")
        return value


    def _append_audit_log(entry: dict) -> None:
        log_path = _workspace() / "tool_audit.jsonl"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\\n")


    def _print(payload: dict) -> None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


    def _build_parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(description="GeminiBot local tool bridge")
        subparsers = parser.add_subparsers(dest="command", required=True)

        memory_search = subparsers.add_parser("memory_search")
        memory_search.add_argument("--query", required=True)
        memory_search.add_argument("--limit", type=int, default=10)

        memory_list = subparsers.add_parser("memory_list_by_date")
        memory_list.add_argument("--start-date", required=True)
        memory_list.add_argument("--end-date", required=True)

        memory_save = subparsers.add_parser("memory_save")
        memory_save.add_argument("--content", required=True)

        schedule_task = subparsers.add_parser("schedule_task")
        schedule_task.add_argument("--schedule-type", choices=["once", "cron"], required=True)
        schedule_task.add_argument("--schedule-value", required=True)
        schedule_task.add_argument("--prompt", required=True)
        schedule_task.add_argument("--timezone")

        list_tasks = subparsers.add_parser("list_tasks")
        list_tasks.add_argument("--chat-id")

        delete_task = subparsers.add_parser("delete_task")
        delete_task.add_argument("--task-id", required=True)
        return parser


    def _run(args: argparse.Namespace) -> dict:
        config = AppConfig.load()
        memory_tools = MemoryTools(config)
        scheduler_tools = SchedulerTools(config)
        conversation_id = _context("GEMINIBOT_CONVERSATION_ID")
        chat_id = os.getenv("GEMINIBOT_CHAT_ID", "").strip()
        user_id = os.getenv("GEMINIBOT_USER_ID", "").strip()
        default_timezone = os.getenv("GEMINIBOT_TIMEZONE", "").strip() or config.default_timezone

        if args.command == "memory_search":
            result = memory_tools.memory_search(
                conversation_id=conversation_id,
                query=args.query,
                limit=args.limit,
            )
        elif args.command == "memory_list_by_date":
            result = memory_tools.memory_list_by_date(
                conversation_id=conversation_id,
                start_date=args.start_date,
                end_date=args.end_date,
            )
        elif args.command == "memory_save":
            result = memory_tools.memory_save(
                conversation_id=conversation_id,
                content=args.content,
            )
        elif args.command == "schedule_task":
            result = scheduler_tools.schedule_task(
                chat_id=chat_id or _context("GEMINIBOT_CHAT_ID"),
                conversation_id=conversation_id,
                prompt=args.prompt,
                schedule_type=args.schedule_type,
                schedule_value=args.schedule_value,
                created_by=user_id or _context("GEMINIBOT_USER_ID"),
                timezone=args.timezone or default_timezone,
            )
        elif args.command == "list_tasks":
            result = scheduler_tools.list_tasks(chat_id=args.chat_id or chat_id or None)
        else:
            result = scheduler_tools.delete_task(task_id=args.task_id)

        return {
            "ok": True,
            "command": args.command,
            "result": result,
        }


    def main() -> None:
        argv = sys.argv[1:]
        parser = _build_parser()
        try:
            payload = _run(parser.parse_args(argv))
            status = 0
        except Exception as exc:
            payload = {
                "ok": False,
                "error": str(exc),
            }
            status = 1

        _append_audit_log(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "argv": argv,
                **payload,
            }
        )
        _print(payload)
        raise SystemExit(status)


    if __name__ == "__main__":
        main()
    """
)

TOOL_BRIDGE_README = dedent(
    """\
    # GeminiBot Tool Bridge

    Use these local commands from the workspace shell when you need to read/write memory or manage schedules.

    Command format:
    `python tools/tool_bridge.py <command> ...`

    The current conversation context is injected automatically through environment variables, so memory commands do not need manual chat or conversation identifiers.

    ## Commands

    ### `memory_search`
    - input: `--query <text>` `--limit <int=10>`
    - output: JSON `{ "ok": true, "command": "memory_search", "result": ["<file>: <line>", ...] }`

    ### `memory_list_by_date`
    - input: `--start-date YYYY-MM-DD` `--end-date YYYY-MM-DD`
    - output: JSON `{ "ok": true, "command": "memory_list_by_date", "result": ["<log contents>", ...] }`

    ### `memory_save`
    - input: `--content <text>`
    - output: JSON `{ "ok": true, "command": "memory_save", "result": "<saved text>" }`

    ### `schedule_task`
    - input: `--schedule-type once|cron` `--schedule-value <ISO datetime or cron>` `--prompt <text>` `[--timezone <IANA tz>]`
    - output: JSON `{ "ok": true, "command": "schedule_task", "result": {"id": "...", "next_run_at": "..."} }`

    ### `list_tasks`
    - input: optional `--chat-id <chat id>`
    - output: JSON `{ "ok": true, "command": "list_tasks", "result": [{...}, ...] }`

    ### `delete_task`
    - input: `--task-id <task id>`
    - output: JSON `{ "ok": true, "command": "delete_task", "result": true|false }`

    ## Audit Log

    Every invocation is appended to `tool_audit.jsonl` in the workspace for operator inspection.
    """
)


class WorkspaceManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.project_root = config.workspace_root.parent
        self.templates_root = self.project_root / "templates"

    def ensure_workspace(self, conversation_id: str) -> Path:
        workspace_dir = self.config.workspace_root / conversation_id
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "logs").mkdir(exist_ok=True)
        (workspace_dir / "summaries").mkdir(exist_ok=True)
        (workspace_dir / "tools").mkdir(exist_ok=True)

        for filename in TEMPLATE_FILES:
            target = workspace_dir / filename
            source = self.templates_root / filename
            if not target.exists() and source.exists():
                shutil.copyfile(source, target)

        self._write_tool_bridge(workspace_dir)
        return workspace_dir

    def _write_tool_bridge(self, workspace_dir: Path) -> None:
        tools_dir = workspace_dir / "tools"
        script_path = tools_dir / "tool_bridge.py"
        script_path.write_text(TOOL_BRIDGE_SCRIPT, encoding="utf-8")
        script_path.chmod(0o755)
        (tools_dir / "README.md").write_text(TOOL_BRIDGE_README.rstrip() + "\n", encoding="utf-8")
