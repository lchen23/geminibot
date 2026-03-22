from __future__ import annotations

from app.config import AppConfig
from app.scheduler.store import SchedulerStore


class SchedulerTools:
    def __init__(self, config: AppConfig) -> None:
        self.store = SchedulerStore(config)

    def schedule_task(self, **kwargs) -> dict:
        return self.store.create_task(**kwargs)

    def list_tasks(self, **kwargs) -> list[dict]:
        return self.store.list_tasks(**kwargs)

    def delete_task(self, task_id: str) -> bool:
        return self.store.delete_task(task_id)
