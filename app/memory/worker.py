from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from queue import Empty, PriorityQueue
from threading import Lock, Thread
from typing import Callable

from app.config import AppConfig
from app.memory.consolidate import generate_workspace_summaries, merge_workspace_memory
from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)


TASK_PRIORITY_APPEND_LOG = 0
TASK_PRIORITY_SAVE_NOTE = 1
TASK_PRIORITY_GENERATE_SUMMARIES = 2
TASK_PRIORITY_MERGE_MEMORY = 3


@dataclass(slots=True)
class MemoryTask:
    conversation_id: str
    description: str
    run: Callable[[], None]
    refresh_snapshot: bool = False
    priority: int = TASK_PRIORITY_SAVE_NOTE
    note_content: str | None = None


class MemoryWorker:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = MemoryStore(config)
        self._queues: dict[str, PriorityQueue[tuple[int, int, MemoryTask | None]]] = {}
        self._threads: dict[str, Thread] = {}
        self._summary_futures: dict[str, Future[None]] = {}
        self._summary_dirty: set[str] = set()
        self._started = False
        self._lock = Lock()
        self._sequence = count()
        self._heavy_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory-heavy")
        self._executor_shutdown = False

    def start(self) -> None:
        if self._started:
            return
        if self._executor_shutdown:
            self._heavy_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory-heavy")
            self._executor_shutdown = False
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        with self._lock:
            self._started = False
            queues = list(self._queues.values())
            threads = list(self._threads.values())
            for queue in queues:
                queue.put((TASK_PRIORITY_MERGE_MEMORY + 1, next(self._sequence), None))
        for thread in threads:
            thread.join()
        self._heavy_executor.shutdown(wait=True)
        with self._lock:
            self._executor_shutdown = True
            self._queues.clear()
            self._threads.clear()
            self._summary_futures.clear()
            self._summary_dirty.clear()

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
                priority=TASK_PRIORITY_APPEND_LOG,
            )
        )

    def submit_save_memory_note(self, conversation_id: str, content: str) -> None:
        self._submit(
            MemoryTask(
                conversation_id=conversation_id,
                description="save_memory_note",
                run=lambda: self.store.save_memory_notes(conversation_id, [content]),
                refresh_snapshot=True,
                priority=TASK_PRIORITY_SAVE_NOTE,
                note_content=content,
            )
        )

    def submit_generate_workspace_summaries(self, conversation_id: str) -> None:
        self._submit(
            MemoryTask(
                conversation_id=conversation_id,
                description="generate_workspace_summaries",
                run=lambda: self._dispatch_generate_workspace_summaries(conversation_id),
                refresh_snapshot=False,
                priority=TASK_PRIORITY_GENERATE_SUMMARIES,
            )
        )

    def submit_merge_workspace_memory(self, conversation_id: str) -> None:
        self._submit(
            MemoryTask(
                conversation_id=conversation_id,
                description="merge_workspace_memory",
                run=lambda: self._merge_workspace_memory(conversation_id),
                refresh_snapshot=True,
                priority=TASK_PRIORITY_MERGE_MEMORY,
            )
        )

    def submit_consolidate_workspace_memory(self, conversation_id: str) -> None:
        self.submit_generate_workspace_summaries(conversation_id)

    def _submit(self, task: MemoryTask) -> None:
        if not self._started:
            raise RuntimeError("MemoryWorker must be started before submitting tasks.")
        self._queue_for_conversation(task.conversation_id).put((task.priority, next(self._sequence), task))

    def _queue_for_conversation(self, conversation_id: str) -> PriorityQueue[tuple[int, int, MemoryTask | None]]:
        with self._lock:
            queue = self._queues.get(conversation_id)
            if queue is not None:
                return queue
            queue = PriorityQueue()
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

    def _run_loop(self, conversation_id: str, queue: PriorityQueue[tuple[int, int, MemoryTask | None]]) -> None:
        while True:
            _, _, task = queue.get()
            try:
                if task is None:
                    return
                stop_requested = False
                if task.description == "save_memory_note" and task.note_content is not None:
                    batch = [task.note_content]
                    while True:
                        try:
                            _, _, queued_task = queue.get_nowait()
                        except Empty:
                            break
                        if queued_task is None:
                            stop_requested = True
                            queue.task_done()
                            break
                        if queued_task.description == "save_memory_note" and queued_task.note_content is not None:
                            batch.append(queued_task.note_content)
                            queue.task_done()
                            continue
                        queue.put((queued_task.priority, next(self._sequence), queued_task))
                        queue.task_done()
                        break
                    self.store.save_memory_notes(task.conversation_id, batch)
                else:
                    task.run()
                if task.refresh_snapshot:
                    self.store.refresh_snapshot(task.conversation_id)
                if stop_requested:
                    return
            except Exception:  # pragma: no cover - defensive logging
                logger.exception(
                    "Memory task failed: %s for conversation %s",
                    task.description,
                    task.conversation_id,
                )
            finally:
                queue.task_done()


    def _dispatch_generate_workspace_summaries(self, conversation_id: str) -> None:
        with self._lock:
            if conversation_id in self._summary_futures:
                self._summary_dirty.add(conversation_id)
                return
            future = self._heavy_executor.submit(self._run_generate_workspace_summaries, conversation_id)
            self._summary_futures[conversation_id] = future
            future.add_done_callback(lambda completed, cid=conversation_id: self._on_summary_generation_done(cid, completed))

    def _run_generate_workspace_summaries(self, conversation_id: str) -> None:
        workspace = self.store.get_workspace(conversation_id)
        generate_workspace_summaries(workspace, config=self.config)

    def _on_summary_generation_done(self, conversation_id: str, future: Future[None]) -> None:
        rerun = False
        with self._lock:
            self._summary_futures.pop(conversation_id, None)
            rerun = conversation_id in self._summary_dirty
            if rerun:
                self._summary_dirty.remove(conversation_id)
        try:
            future.result()
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Summary generation failed for conversation %s", conversation_id)
            if rerun and self._started:
                self.submit_generate_workspace_summaries(conversation_id)
            return
        if rerun and self._started:
            self.submit_generate_workspace_summaries(conversation_id)
            return
        if self._started:
            self.submit_merge_workspace_memory(conversation_id)

    def _merge_workspace_memory(self, conversation_id: str) -> None:
        workspace = self.store.get_workspace(conversation_id)
        merge_workspace_memory(workspace, config=self.config)

    @staticmethod
    def workspace_for_conversation(store: MemoryStore, conversation_id: str) -> Path:
        return store.get_workspace(conversation_id)
