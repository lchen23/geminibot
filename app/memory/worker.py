from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
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
    refresh_snapshot: bool = False


class MemoryWorker:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = MemoryStore(config)
        self._queues: dict[str, Queue[MemoryTask | None]] = {}
        self._threads: dict[str, Thread] = {}
        self._started = False
        self._lock = Lock()

    def start(self) -> None:
        if self._started:
            return
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        with self._lock:
            queues = list(self._queues.values())
            threads = list(self._threads.values())
            for queue in queues:
                queue.put(None)
        for thread in threads:
            thread.join()
        with self._lock:
            self._queues.clear()
            self._threads.clear()
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
                refresh_snapshot=False,
            )
        )

    def submit_save_memory_note(self, conversation_id: str, content: str) -> None:
        self._submit(
            MemoryTask(
                conversation_id=conversation_id,
                description="save_memory_note",
                run=lambda: self.store.save_memory_note(conversation_id, content),
                refresh_snapshot=True,
            )
        )

    def submit_consolidate_workspace_memory(self, conversation_id: str) -> None:
        self._submit(
            MemoryTask(
                conversation_id=conversation_id,
                description="consolidate_workspace_memory",
                run=lambda: self._consolidate(conversation_id),
                refresh_snapshot=True,
            )
        )

    def _submit(self, task: MemoryTask) -> None:
        if not self._started:
            raise RuntimeError("MemoryWorker must be started before submitting tasks.")
        self._queue_for_conversation(task.conversation_id).put(task)

    def _queue_for_conversation(self, conversation_id: str) -> Queue[MemoryTask | None]:
        with self._lock:
            queue = self._queues.get(conversation_id)
            if queue is not None:
                return queue
            queue = Queue()
            thread = Thread(
                target=self._run_loop,
                args=(conversation_id, queue),
                name=f"memory-worker-{conversation_id}",
                daemon=True,
            )
            self._queues[conversation_id] = queue
            self._threads[conversation_id] = thread
            thread.start()
            return queue

    def _run_loop(self, conversation_id: str, queue: Queue[MemoryTask | None]) -> None:
        while True:
            task = queue.get()
            try:
                if task is None:
                    return
                task.run()
                if task.refresh_snapshot:
                    self.store.refresh_snapshot(task.conversation_id)
            except Exception:  # pragma: no cover - defensive logging
                logger.exception(
                    "Memory task failed: %s for conversation %s",
                    task.description,
                    task.conversation_id,
                )
            finally:
                queue.task_done()


    def _consolidate(self, conversation_id: str) -> None:
        workspace = self.store.get_workspace(conversation_id)
        consolidate_workspace_memory(workspace, config=self.config)

    @staticmethod
    def workspace_for_conversation(store: MemoryStore, conversation_id: str) -> Path:
        return store.get_workspace(conversation_id)
