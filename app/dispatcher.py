from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterator

from app.agent.engine import GeminiAgentEngine, AgentRequest
from app.config import AppConfig
from app.memory.consolidate import consolidate_workspace_memory
from app.memory.store import MemoryStore
from app.rendering.cards import build_markdown_reply
from app.scheduler.store import SchedulerStore


@dataclass(slots=True)
class IncomingMessage:
    message_id: str
    chat_id: str
    user_id: str
    conversation_id: str
    text: str
    sent_at: str
    source: str = "feishu"
    chat_type: str | None = None


class Dispatcher:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.memory_store = MemoryStore(config)
        self.scheduler_store = SchedulerStore(config)
        self.agent = GeminiAgentEngine(config=config, memory_store=self.memory_store)

    def handle(self, message: IncomingMessage) -> dict:
        reply = self._resolve_reply_text(message)
        self._append_reply_log(message, reply)
        footer = None
        if message.source == "scheduler":
            footer = "Scheduled task"
        return build_markdown_reply(reply, footer=footer)

    def stream_handle(self, message: IncomingMessage) -> Iterator[str]:
        command_reply = self._resolve_builtin_reply_text(message)
        if command_reply is not None:
            self._append_reply_log(message, command_reply)
            yield command_reply
            return

        agent_request = AgentRequest(
            conversation_id=message.conversation_id,
            chat_id=message.chat_id,
            user_id=message.user_id,
            text=message.text,
            source=message.source,
        )
        final_text = ""
        for event in self.agent.stream(agent_request):
            if event.error and event.text:
                final_text = event.text
            elif event.text:
                final_text = event.text
            if event.delta:
                yield event.text
        if not final_text:
            final_text = "Gemini CLI returned no output."
            yield final_text
        self._append_reply_log(message, final_text)

    def dispatch_scheduled_task(self, task: dict) -> dict:
        return self.handle(
            IncomingMessage(
                message_id=f"scheduled-{task['id']}-{datetime.now(UTC).timestamp()}",
                chat_id=task["chat_id"],
                user_id=task["created_by"],
                conversation_id=task["conversation_id"],
                text=task["prompt"],
                sent_at=datetime.now(UTC).isoformat(),
                source="scheduler",
                chat_type="p2p",
            )
        )

    def _resolve_reply_text(self, message: IncomingMessage) -> str:
        command_reply = self._resolve_builtin_reply_text(message)
        if command_reply is not None:
            return command_reply

        result = self.agent.run(
            AgentRequest(
                conversation_id=message.conversation_id,
                chat_id=message.chat_id,
                user_id=message.user_id,
                text=message.text,
                source=message.source,
            )
        )
        return result.text

    def _resolve_builtin_reply_text(self, message: IncomingMessage) -> str | None:
        text = message.text.strip()
        if text == "/help":
            return self._help_text()
        if text == "/clear":
            return self._handle_clear(message.conversation_id)
        if text.startswith("/remember "):
            content = text.removeprefix("/remember ").strip()
            self.memory_store.save_memory_note(message.conversation_id, content)
            return f"Noted: {content}"
        if text == "/tasks":
            return self._format_tasks(self.scheduler_store.list_tasks(chat_id=message.chat_id))
        if text.startswith("/schedule "):
            return self._handle_schedule(message, text.removeprefix("/schedule ").strip())
        if text.startswith("/delete-task "):
            task_id = text.removeprefix("/delete-task ").strip()
            deleted = self.scheduler_store.delete_task(task_id)
            return f"Deleted task: {task_id}" if deleted else f"Task not found: {task_id}"
        return None

    def _append_reply_log(self, message: IncomingMessage, reply: str) -> None:
        self.memory_store.append_daily_log(
            conversation_id=message.conversation_id,
            user_text=message.text,
            assistant_text=reply,
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
                "- /help — Show this help message.",
                "- /clear — Clear the current conversation context and consolidate memory.",
                "- /remember <text> — Save a memory note for this conversation.",
                "- /tasks — List scheduled tasks for the current chat.",
                "- /schedule <once|cron> | <time-or-cron> | <prompt> — Create a one-time or recurring scheduled task.",
                "- /delete-task <task_id> — Delete a scheduled task by ID.",
            ]
        )
