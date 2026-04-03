from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Callable

from app.config import AppConfig
from app.memory.consolidate import consolidate_workspace_memory
from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MemoryTask:
    conversation_id: str
    description: str
    run: Callable[[], None]


class MemoryWorker:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = MemoryStore(config)
        self._queue: Queue[MemoryTask | None] = Queue()
        self._thread = Thread(target=self._run_loop, name="memory-worker", daemon=True)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def stop(self) -> None:
        if not self._started:
            return
        self._queue.put(None)
        self._thread.join()
        self._started = False

    def submit_append_daily_log(self, conversation_id: str, user_text: str, assistant_text: str) -> None:
        self._submit(
            MemoryTask(
                conversation_id=conversation_id,
                description="append_daily_log",
                run=lambda: self.store.append_daily_log(
                    conversation_id=conversation_id,
                    user_text=user_text,
                    assistant_text=assistant_text,
                ),
            )
        )

    def submit_save_memory_note(self, conversation_id: str, content: str) -> None:
        self._submit(
            MemoryTask(
                conversation_id=conversation_id,
                description="save_memory_note",
                run=lambda: self.store.save_memory_note(conversation_id, content),
            )
        )

    def submit_consolidate_workspace_memory(self, conversation_id: str) -> None:
        self._submit(
            MemoryTask(
                conversation_id=conversation_id,
                description="consolidate_workspace_memory",
                run=lambda: self._consolidate(conversation_id),
            )
        )

    def _submit(self, task: MemoryTask) -> None:
        if not self._started:
            raise RuntimeError("MemoryWorker must be started before submitting tasks.")
        self._queue.put(task)

    def _run_loop(self) -> None:
        while True:
            task = self._queue.get()
            try:
                if task is None:
                    return
                task.run()
            except Exception:  # pragma: no cover - defensive logging
                logger.exception(
                    "Memory task failed: %s for conversation %s",
                    task.description,
                    task.conversation_id,
                )
            finally:
                self._queue.task_done()

    def _consolidate(self, conversation_id: str) -> None:
        workspace = self.store.get_workspace(conversation_id)
        consolidate_workspace_memory(workspace, config=self.config)

    @staticmethod
    def workspace_for_conversation(store: MemoryStore, conversation_id: str) -> Path:
        return store.get_workspace(conversation_id)
