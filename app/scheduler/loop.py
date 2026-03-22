from __future__ import annotations

import logging
import threading
import time

from app.config import AppConfig
from app.dispatcher import Dispatcher
from app.scheduler.store import SchedulerStore

logger = logging.getLogger(__name__)


class SchedulerLoop:
    def __init__(self, config: AppConfig, dispatcher: Dispatcher) -> None:
        self.config = config
        self.dispatcher = dispatcher
        self.store = SchedulerStore(config)
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Scheduler loop started.")

    def _run(self) -> None:
        while self._running:
            time.sleep(self.config.poll_interval_seconds)
            # Placeholder for due-task evaluation and dispatch.
