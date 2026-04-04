from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.config import StartupCheckResult
from app.main import run_service


class MainLifecycleTests(unittest.TestCase):
    def test_run_service_starts_and_stops_memory_worker(self) -> None:
        config = MagicMock()
        config.log_level = "INFO"
        config.run_startup_checks.return_value = StartupCheckResult(warnings=[])

        memory_worker = MagicMock()
        gateway = MagicMock()
        scheduler = MagicMock()

        with patch("app.main.AppConfig.load", return_value=config), \
            patch("app.main.configure_logging"), \
            patch("app.main.MemoryWorker", return_value=memory_worker), \
            patch("app.main.Dispatcher", return_value=MagicMock()), \
            patch("app.main.FeishuGateway", return_value=gateway), \
            patch("app.main.SchedulerLoop", return_value=scheduler), \
            patch("app.main.time.sleep", side_effect=KeyboardInterrupt):
            run_service()

        memory_worker.start.assert_called_once_with()
        gateway.start.assert_called_once_with()
        scheduler.start.assert_called_once_with()
        gateway.stop.assert_called_once_with()
        scheduler.stop.assert_called_once_with()
        memory_worker.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
