from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from croniter import croniter

from app.config import AppConfig
from app.utils.state import JsonListState


class SchedulerStore:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.state = JsonListState(config.data_root / "schedules.json")

    def list_tasks(self, chat_id: str | None = None) -> list[dict[str, Any]]:
        tasks = self.state.read()
        if chat_id is None:
            return tasks
        return [task for task in tasks if task.get("chat_id") == chat_id]

    def get_due_tasks(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.utcnow()
        due: list[dict[str, Any]] = []
        for task in self.list_tasks():
            if not task.get("enabled", True):
                continue
            next_run_at = task.get("next_run_at")
            if not next_run_at:
                continue
            if datetime.fromisoformat(next_run_at) <= now:
                due.append(task)
        return due

    def create_task(
        self,
        *,
        chat_id: str,
        conversation_id: str,
        prompt: str,
        schedule_type: str,
        schedule_value: str,
        created_by: str,
        timezone: str,
    ) -> dict[str, Any]:
        now = datetime.utcnow()
        task = {
            "id": uuid4().hex,
            "chat_id": chat_id,
            "conversation_id": conversation_id,
            "prompt": prompt,
            "schedule_type": schedule_type,
            "schedule_value": schedule_value,
            "timezone": timezone,
            "next_run_at": self._compute_next_run(schedule_type, schedule_value, now),
            "created_by": created_by,
            "enabled": True,
            "last_run_at": None,
        }
        tasks = self.list_tasks()
        tasks.append(task)
        self.save_tasks(tasks)
        return task

    def mark_task_executed(self, task_id: str, run_at: datetime | None = None) -> dict[str, Any] | None:
        run_at = run_at or datetime.utcnow()
        tasks = self.list_tasks()
        updated_task: dict[str, Any] | None = None
        remaining: list[dict[str, Any]] = []

        for task in tasks:
            if task.get("id") != task_id:
                remaining.append(task)
                continue

            task["last_run_at"] = run_at.isoformat()
            if task.get("schedule_type") == "once":
                updated_task = task
                continue

            task["next_run_at"] = self._compute_next_run(task["schedule_type"], task["schedule_value"], run_at)
            updated_task = task
            remaining.append(task)

        self.save_tasks(remaining)
        return updated_task

    def delete_task(self, task_id: str) -> bool:
        tasks = self.list_tasks()
        remaining = [task for task in tasks if task.get("id") != task_id]
        if len(remaining) == len(tasks):
            return False
        self.save_tasks(remaining)
        return True

    def save_tasks(self, tasks: list[dict[str, Any]]) -> None:
        self.state.write(tasks)

    def _compute_next_run(self, schedule_type: str, schedule_value: str, now: datetime) -> str:
        if schedule_type == "once":
            return datetime.fromisoformat(schedule_value).isoformat()
        if schedule_type == "cron":
            return croniter(schedule_value, now).get_next(datetime).isoformat()
        raise ValueError(f"Unsupported schedule_type: {schedule_type}")
