from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.agent.engine import GeminiAgentEngine, AgentRequest
from app.config import AppConfig
from app.memory.consolidate import consolidate_workspace_memory
from app.memory.store import MemoryStore
from app.rendering.cards import build_markdown_reply
from app.scheduler.store import SchedulerStore


@dataclass
class IncomingMessage:
    message_id: str
    chat_id: str
    user_id: str
    conversation_id: str
    text: str
    sent_at: str
    source: str = "feishu"


class Dispatcher:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.memory_store = MemoryStore(config)
        self.scheduler_store = SchedulerStore(config)
        self.agent = GeminiAgentEngine(config=config, memory_store=self.memory_store)

    def handle_stream(
        self,
        message: IncomingMessage,
        on_event: Callable[[dict], None] | None = None,
    ) -> tuple[dict, list[dict]]:
        text = message.text.strip()
        events: list[dict] = []

        if text == "/help":
            reply = self._help_text()
        elif text == "/clear":
            reply = self._handle_clear(message.conversation_id)
        elif text.startswith("/remember "):
            content = text.removeprefix("/remember ").strip()
            self.memory_store.save_memory_note(message.conversation_id, content)
            reply = f"Noted: {content}"
        elif text == "/tasks":
            reply = self._format_tasks(self.scheduler_store.list_tasks(chat_id=message.chat_id))
        elif text.startswith("/schedule "):
            reply = self._handle_schedule(message, text.removeprefix("/schedule ").strip())
        elif text.startswith("/delete-task "):
            task_id = text.removeprefix("/delete-task ").strip()
            deleted = self.scheduler_store.delete_task(task_id)
            reply = f"Deleted task: {task_id}" if deleted else f"Task not found: {task_id}"
        else:
            stream_result = self.agent.run_stream(
                AgentRequest(
                    conversation_id=message.conversation_id,
                    chat_id=message.chat_id,
                    user_id=message.user_id,
                    text=message.text,
                    source=message.source,
                ),
                on_event=on_event,
            )
            reply = stream_result.result.text
            events = stream_result.events

        self.memory_store.append_daily_log(
            conversation_id=message.conversation_id,
            user_text=message.text,
            assistant_text=reply,
        )
        footer = None
        if message.source == "scheduler":
            footer = "Scheduled task"
        return build_markdown_reply(reply, footer=footer), events

    def handle(self, message: IncomingMessage) -> dict:
        payload, _events = self.handle_stream(message)
        return payload

    def dispatch_scheduled_task(self, task: dict) -> dict:
        return self.handle(
            IncomingMessage(
                message_id=f"scheduled-{task['id']}-{datetime.utcnow().timestamp()}",
                chat_id=task["chat_id"],
                user_id=task["created_by"],
                conversation_id=task["conversation_id"],
                text=task["prompt"],
                sent_at=datetime.utcnow().isoformat(),
                source="scheduler",
            )
        )

    def _handle_clear(self, conversation_id: str) -> str:
        workspace = self.memory_store.get_workspace(conversation_id)
        consolidate_workspace_memory(workspace)
        self.agent.clear_conversation(conversation_id)
        return "Conversation context cleared. Memory was consolidated from current logs."

    def _handle_schedule(self, message: IncomingMessage, payload: str) -> str:
        if "|" not in payload:
            return "Usage: /schedule <once|cron> | <time-or-cron> | <prompt>"

        parts = [part.strip() for part in payload.split("|", 2)]
        if len(parts) != 3 or not all(parts):
            return "Usage: /schedule <once|cron> | <time-or-cron> | <prompt>"

        schedule_type, schedule_value, prompt = parts
        if schedule_type not in {"once", "cron"}:
            return "schedule_type must be 'once' or 'cron'."

        try:
            task = self.scheduler_store.create_task(
                chat_id=message.chat_id,
                conversation_id=message.conversation_id,
                prompt=prompt,
                schedule_type=schedule_type,
                schedule_value=schedule_value,
                created_by=message.user_id,
                timezone=self.config.default_timezone,
            )
        except ValueError as exc:
            return f"Invalid schedule: {exc}"

        return f"Scheduled task {task['id']} for {task['next_run_at']}: {task['prompt']}"

    def _format_tasks(self, tasks: list[dict]) -> str:
        if not tasks:
            return "No tasks scheduled."
        return "\n".join(
            f"- {task['id']}: [{task['schedule_type']}] {task['prompt']} @ {task['next_run_at']}"
            for task in tasks
        )

    def _help_text(self) -> str:
        return "\n".join(
            [
                "Supported commands:",
                "- /help",
                "- /clear",
                "- /remember <text>",
                "- /tasks",
                "- /schedule <once|cron> | <time-or-cron> | <prompt>",
                "- /delete-task <task_id>",
            ]
        )
