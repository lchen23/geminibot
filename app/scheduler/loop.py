from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Callable

from app.config import AppConfig
from app.dispatcher import Dispatcher
from app.scheduler.store import SchedulerStore
from app.utils.state import JsonListState

logger = logging.getLogger(__name__)


class SchedulerLoop:
    def __init__(self, config: AppConfig, dispatcher: Dispatcher, deliver_message: Callable[[str, dict], None]) -> None:
        self.config = config
        self.dispatcher = dispatcher
        self.deliver_message = deliver_message
        self.store = SchedulerStore(config)
        self.execution_log = JsonListState(config.data_root / "schedule_runs.json")
        self.lock_stale_after_seconds = 600
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Scheduler loop started.")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=self.config.poll_interval_seconds + 1)
        logger.info("Scheduler loop stopped.")

    def _run(self) -> None:
        while self._running:
            self._dispatch_due_tasks()
            time.sleep(self.config.poll_interval_seconds)

    def _dispatch_due_tasks(self) -> None:
        now = datetime.now(UTC)
        due_tasks = self.store.get_due_tasks(now=now)
        if not due_tasks:
            return

        for task in due_tasks:
            task_id = task.get("id", "unknown")
            claimed_task = self.store.claim_task_for_run(
                task_id,
                run_at=now,
                stale_after_seconds=self.lock_stale_after_seconds,
            )
            if claimed_task is None:
                logger.info("Skipping scheduled task id=%s because it is already running.", task_id)
                self._append_execution_log(task=task, status="skipped", run_at=now, error="already_running")
                continue

            logger.info("Dispatching scheduled task id=%s conversation_id=%s", task_id, task.get("conversation_id"))
            try:
                response = self.dispatcher.dispatch_scheduled_task(claimed_task)
                self.deliver_message(claimed_task["chat_id"], response)
                updated_task = self.store.complete_task_run(
                    task_id,
                    run_at=now,
                    run_token=claimed_task["run_token"],
                )
                self._append_execution_log(task=claimed_task, status="success", run_at=now, result=updated_task)
                logger.info("Scheduled task executed id=%s next_run_at=%s", task_id, updated_task.get("next_run_at") if updated_task else None)
            except Exception as exc:
                self.store.fail_task_run(task_id, run_token=claimed_task["run_token"])
                self._append_execution_log(task=claimed_task, status="failed", run_at=now, error=str(exc))
                logger.exception("Scheduled task failed id=%s", task_id)

    def _append_execution_log(
        self,
        *,
        task: dict,
        status: str,
        run_at: datetime,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        logs = self.execution_log.read()
        logs.append(
            {
                "task_id": task.get("id"),
                "chat_id": task.get("chat_id"),
                "conversation_id": task.get("conversation_id"),
                "prompt": task.get("prompt"),
                "schedule_type": task.get("schedule_type"),
                "status": status,
                "run_at": run_at.isoformat(),
                "next_run_at": result.get("next_run_at") if result else None,
                "last_run_at": result.get("last_run_at") if result else None,
                "error": error,
            }
        )
        self.execution_log.write(logs)
