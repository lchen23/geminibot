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
            "running": False,
            "started_at": None,
            "run_token": None,
        }
        tasks = self.list_tasks()
        tasks.append(task)
        self.save_tasks(tasks)
        return task

    def claim_task_for_run(
        self,
        task_id: str,
        *,
        run_at: datetime | None = None,
        stale_after_seconds: int,
    ) -> dict[str, Any] | None:
        run_at = run_at or datetime.utcnow()
        tasks = self.list_tasks()
        claimed_task: dict[str, Any] | None = None

        for task in tasks:
            if task.get("id") != task_id or not task.get("enabled", True):
                continue
            if task.get("running") and not self._is_stale_lock(task, run_at, stale_after_seconds):
                return None

            task["running"] = True
            task["started_at"] = run_at.isoformat()
            task["run_token"] = uuid4().hex
            claimed_task = dict(task)
            break

        if claimed_task is None:
            return None

        self.save_tasks(tasks)
        return claimed_task

    def complete_task_run(
        self,
        task_id: str,
        *,
        run_at: datetime | None = None,
        run_token: str,
    ) -> dict[str, Any] | None:
        run_at = run_at or datetime.utcnow()
        tasks = self.list_tasks()
        updated_task: dict[str, Any] | None = None
        remaining: list[dict[str, Any]] = []

        for task in tasks:
            if task.get("id") != task_id:
                remaining.append(task)
                continue
            if task.get("run_token") != run_token:
                remaining.append(task)
                continue

            task["last_run_at"] = run_at.isoformat()
            task["running"] = False
            task["started_at"] = None
            task["run_token"] = None
            if task.get("schedule_type") == "once":
                updated_task = dict(task)
                continue

            task["next_run_at"] = self._compute_next_run(task["schedule_type"], task["schedule_value"], run_at)
            updated_task = dict(task)
            remaining.append(task)

        self.save_tasks(remaining)
        return updated_task

    def fail_task_run(self, task_id: str, *, run_token: str) -> dict[str, Any] | None:
        tasks = self.list_tasks()
        updated_task: dict[str, Any] | None = None

        for task in tasks:
            if task.get("id") != task_id or task.get("run_token") != run_token:
                continue
            task["running"] = False
            task["started_at"] = None
            task["run_token"] = None
            updated_task = dict(task)
            break

        if updated_task is None:
            return None

        self.save_tasks(tasks)
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

    def _is_stale_lock(self, task: dict[str, Any], run_at: datetime, stale_after_seconds: int) -> bool:
        started_at = task.get("started_at")
        if not started_at:
            return True
        try:
            started = datetime.fromisoformat(started_at)
        except ValueError:
            return True
        return (run_at - started).total_seconds() > stale_after_seconds
