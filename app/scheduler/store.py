from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from app.config import AppConfig
from app.utils.state import JsonListState


class SchedulerStore:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.state = JsonListState(config.data_root / "schedules.json")
        self.default_zone = self._load_zone(config.default_timezone)

    def list_tasks(self, chat_id: str | None = None) -> list[dict[str, Any]]:
        tasks = self.state.read()
        if chat_id is None:
            return tasks
        return [task for task in tasks if task.get("chat_id") == chat_id]

    def get_due_tasks(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now_utc = self._as_utc(now)
        due: list[dict[str, Any]] = []
        for task in self.list_tasks():
            if not task.get("enabled", True):
                continue
            next_run_at = task.get("next_run_at")
            if not next_run_at:
                continue
            if self._parse_utc(next_run_at) <= now_utc:
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
        now = datetime.now(UTC)
        normalized_timezone = timezone or self.config.default_timezone
        task = {
            "id": uuid4().hex,
            "chat_id": chat_id,
            "conversation_id": conversation_id,
            "prompt": prompt,
            "schedule_type": schedule_type,
            "schedule_value": schedule_value,
            "timezone": normalized_timezone,
            "next_run_at": self._compute_next_run(
                schedule_type,
                schedule_value,
                now,
                timezone=normalized_timezone,
            ),
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
        run_at = self._as_utc(run_at)
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
        run_at = self._as_utc(run_at)
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

            task["next_run_at"] = self._compute_next_run(
                task["schedule_type"],
                task["schedule_value"],
                run_at,
                timezone=task.get("timezone"),
            )
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

    def _compute_next_run(
        self,
        schedule_type: str,
        schedule_value: str,
        now: datetime,
        *,
        timezone: str | None,
    ) -> str:
        zone = self._resolve_zone(timezone)
        if schedule_type == "once":
            return self._parse_schedule_datetime(schedule_value, zone).isoformat()
        if schedule_type == "cron":
            local_now = self._as_utc(now).astimezone(zone)
            return croniter(schedule_value, local_now).get_next(datetime).astimezone(UTC).isoformat()
        raise ValueError(f"Unsupported schedule_type: {schedule_type}")

    def _is_stale_lock(self, task: dict[str, Any], run_at: datetime, stale_after_seconds: int) -> bool:
        started_at = task.get("started_at")
        if not started_at:
            return True
        try:
            started = self._parse_utc(started_at)
        except ValueError:
            return True
        return (run_at - started).total_seconds() > stale_after_seconds

    def _resolve_zone(self, timezone: str | None) -> ZoneInfo:
        if timezone:
            return self._load_zone(timezone)
        return self.default_zone

    def _load_zone(self, timezone: str) -> ZoneInfo:
        try:
            return ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unsupported timezone: {timezone}") from exc

    def _parse_schedule_datetime(self, value: str, zone: ZoneInfo) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=zone).astimezone(UTC)
        return parsed.astimezone(UTC)

    def _parse_utc(self, value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _as_utc(self, value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            return current.replace(tzinfo=UTC)
        return current.astimezone(UTC)
