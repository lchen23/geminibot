from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.config import AppConfig
from app.dispatcher import Dispatcher
from app.gateway.feishu import FeishuGateway
from app.memory.worker import MemoryWorker
from app.scheduler.loop import SchedulerLoop


def _load_repo_env() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[3] / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _setting(name: str, repo_env: dict[str, str], default: str = "") -> str:
    return os.getenv(name, repo_env.get(name, default))


class RealSchedulerFlowE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        repo_env = _load_repo_env()
        if not _setting("GEMINIBOT_E2E_REAL", repo_env):
            raise unittest.SkipTest("Set GEMINIBOT_E2E_REAL=1 to run real Feishu + Gemini E2E tests.")

        cls.repo_env = repo_env
        cls.chat_id = os.getenv("GEMINIBOT_E2E_FEISHU_CHAT_ID", "")
        cls.user_id = os.getenv("GEMINIBOT_E2E_FEISHU_USER_ID", "")
        cls.conversation_id = os.getenv("GEMINIBOT_E2E_CONVERSATION_ID", cls.chat_id)

        missing = [
            name
            for name, value in {
                "FEISHU_APP_ID": _setting("FEISHU_APP_ID", repo_env),
                "FEISHU_APP_SECRET": _setting("FEISHU_APP_SECRET", repo_env),
                "GEMINI_CLI_PATH": _setting("GEMINI_CLI_PATH", repo_env, "gemini"),
                "GEMINIBOT_E2E_FEISHU_CHAT_ID": cls.chat_id,
                "GEMINIBOT_E2E_FEISHU_USER_ID": cls.user_id,
            }.items()
            if not value
        ]
        if missing:
            raise unittest.SkipTest(f"Missing real E2E settings: {', '.join(missing)}")

        gemini_cli_path = _setting("GEMINI_CLI_PATH", repo_env, "gemini")
        if shutil.which(gemini_cli_path) is None:
            raise unittest.SkipTest(f"Gemini CLI not found on PATH: {gemini_cli_path}")

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.config = AppConfig(
            feishu_app_id=_setting("FEISHU_APP_ID", self.repo_env),
            feishu_app_secret=_setting("FEISHU_APP_SECRET", self.repo_env),
            gemini_api_key=_setting("GEMINI_API_KEY", self.repo_env),
            ai_provider="gemini",
            gemini_cli_path=_setting("GEMINI_CLI_PATH", self.repo_env, "gemini"),
            claude_cli_path=_setting("CLAUDE_CLI_PATH", self.repo_env, "claude"),
            bot_name=_setting("BOT_NAME", self.repo_env, "GeminiBot"),
            default_timezone=_setting("DEFAULT_TIMEZONE", self.repo_env, "UTC"),
            app_root=root,
            poll_interval_seconds=1,
            recent_summary_days=int(_setting("RECENT_SUMMARY_DAYS", self.repo_env, "7")),
            card_footer_enabled=_setting("CARD_FOOTER_ENABLED", self.repo_env, "true").lower() == "true",
            log_level=_setting("LOG_LEVEL", self.repo_env, "INFO"),
        )
        self.config.ensure_directories()
        self.memory_worker = MemoryWorker(self.config)
        self.memory_worker.start()
        self.dispatcher = Dispatcher(config=self.config, memory_worker=self.memory_worker)
        self.gateway = FeishuGateway(self.config, self.dispatcher)
        self.gateway._get_tenant_access_token(force_refresh=True)

    def tearDown(self) -> None:
        self.memory_worker.stop()
        self.gateway.stop()
        self.temp_dir.cleanup()

    def _message_id(self) -> str:
        return f"e2e-{uuid4().hex}"

    def _send_via_gateway(self, text: str) -> dict:
        response = self.gateway.handle_text_message(
            message_id=self._message_id(),
            chat_id=self.chat_id,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            text=text,
        )
        self.assertIsNotNone(response)
        assert response is not None
        self.gateway.deliver(self.chat_id, response)
        return response

    def _markdown_content(self, response: dict) -> str:
        return response["body"]["elements"][0]["content"]

    def _read_tasks(self) -> list[dict]:
        return json.loads((self.config.data_root / "schedules.json").read_text(encoding="utf-8"))

    def _read_schedule_runs(self) -> list[dict]:
        runs_file = self.config.data_root / "schedule_runs.json"
        if not runs_file.exists():
            return []
        return json.loads(runs_file.read_text(encoding="utf-8"))

    def _write_tasks(self, tasks: list[dict]) -> None:
        (self.config.data_root / "schedules.json").write_text(
            json.dumps(tasks, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _wait_until(self, predicate, *, timeout: float = 120.0, interval: float = 0.5) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()

    def _today_log_text(self) -> str:
        log_file = self.config.workspace_root / self.conversation_id / "logs" / f"{datetime.now(UTC).date().isoformat()}.md"
        if not log_file.exists():
            return ""
        return log_file.read_text(encoding="utf-8")

    def test_schedule_once_command_creates_real_persisted_task(self) -> None:
        response = self._send_via_gateway(
            "/schedule once | 2099-04-10T09:00:00 | Remind me to review the PR"
        )

        tasks = self._read_tasks()

        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task["schedule_type"], "once")
        self.assertEqual(task["prompt"], "Remind me to review the PR")
        self.assertEqual(task["chat_id"], self.chat_id)
        self.assertEqual(task["created_by"], self.user_id)
        self.assertIn(f"Scheduled task {task['id']}", self._markdown_content(response))

    def test_schedule_once_task_runs_and_is_removed_after_real_dispatch(self) -> None:
        prompt = f"Scheduled once task {uuid4().hex[:8]}"
        self._send_via_gateway(f"/schedule once | 2099-04-10T09:00:00 | {prompt}")
        task = self._read_tasks()[0]
        task["next_run_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        self._write_tasks([task])

        scheduler = SchedulerLoop(self.config, self.dispatcher, self.gateway.deliver)
        scheduler._dispatch_due_tasks()

        self.assertEqual(self._read_tasks(), [])
        runs = self._read_schedule_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "success")
        self.assertEqual(runs[0]["task_id"], task["id"])
        self.assertTrue(self._wait_until(lambda: prompt in self._today_log_text()))
        self.assertFalse((self.config.data_root / "unsent_messages.json").exists())

    def test_schedule_cron_command_creates_real_persisted_task(self) -> None:
        prompt = f"Weekly report reminder {uuid4().hex[:8]}"
        response = self._send_via_gateway(f"/schedule cron | 0 18 * * 5 | {prompt}")

        tasks = self._read_tasks()

        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task["schedule_type"], "cron")
        self.assertEqual(task["schedule_value"], "0 18 * * 5")
        self.assertEqual(task["prompt"], prompt)
        self.assertEqual(task["timezone"], self.config.default_timezone)
        self.assertIn(f"Scheduled task {task['id']}", self._markdown_content(response))

    def test_cron_task_runs_and_advances_next_run_at_after_real_dispatch(self) -> None:
        prompt = f"Frequent check {uuid4().hex[:8]}"
        self._send_via_gateway(f"/schedule cron | * * * * * | {prompt}")
        task = self._read_tasks()[0]
        original_next_run_at = task["next_run_at"]
        task["next_run_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        self._write_tasks([task])

        scheduler = SchedulerLoop(self.config, self.dispatcher, self.gateway.deliver)
        scheduler._dispatch_due_tasks()

        tasks = self._read_tasks()
        runs = self._read_schedule_runs()

        self.assertEqual(len(tasks), 1)
        updated_task = tasks[0]
        self.assertEqual(updated_task["id"], task["id"])
        self.assertEqual(updated_task["schedule_type"], "cron")
        self.assertIsNotNone(updated_task["last_run_at"])
        self.assertFalse(updated_task["running"])
        self.assertIsNone(updated_task["run_token"])
        self.assertNotEqual(updated_task["next_run_at"], task["next_run_at"])
        self.assertNotEqual(updated_task["next_run_at"], original_next_run_at)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "success")
        self.assertTrue(self._wait_until(lambda: prompt in self._today_log_text()))
        self.assertFalse((self.config.data_root / "unsent_messages.json").exists())

    def test_delete_task_command_removes_real_persisted_task(self) -> None:
        prompt = f"Delete task prompt {uuid4().hex[:8]}"
        self._send_via_gateway(f"/schedule once | 2099-04-10T09:00:00 | {prompt}")
        task = self._read_tasks()[0]

        response = self._send_via_gateway(f"/delete-task {task['id']}")

        self.assertEqual(self._read_tasks(), [])
        self.assertIn(f"Deleted task: {task['id']}", self._markdown_content(response))

    def test_invalid_schedule_payload_returns_error_and_does_not_create_task(self) -> None:
        response = self._send_via_gateway("/schedule once | bad-date | test invalid date")

        self.assertEqual(self._read_tasks(), [])
        self.assertIn("Invalid schedule:", self._markdown_content(response))

    def test_invalid_schedule_type_returns_error_and_does_not_create_task(self) -> None:
        response = self._send_via_gateway("/schedule weekly | 0 18 * * 5 | Weekly report reminder")

        self.assertEqual(self._read_tasks(), [])
        self.assertIn("schedule_type must be 'once' or 'cron'.", self._markdown_content(response))


if __name__ == "__main__":
    unittest.main()
