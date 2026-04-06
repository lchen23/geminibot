from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from app.config import AppConfig
from app.scheduler.store import SchedulerStore


MANUAL_WAIT_TIMEOUT_SECONDS = 180.0
MANUAL_POLL_INTERVAL_SECONDS = 1.0


MANUAL_TEST_SEQUENCE: list[tuple[str, str]] = [
    (
        "test_manual_remember_command_persists_memory",
        "Send /remember and verify memory persistence.",
    ),
    (
        "test_manual_schedule_once_command_creates_task",
        "Send /schedule once and verify schedules.json writes a one-time task.",
    ),
    (
        "test_manual_schedule_cron_command_creates_task",
        "Send /schedule cron and verify schedules.json writes a cron task.",
    ),
    (
        "test_manual_tasks_command_logs_current_task_list",
        "Send /tasks and verify the reply is captured in today's log.",
    ),
    (
        "test_manual_delete_task_command_removes_task",
        "Send /delete-task and verify the seeded task is removed.",
    ),
    (
        "test_manual_clear_command_generates_summary",
        "Send /clear and verify summary regeneration or summary-derived metadata growth.",
    ),
]


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


class ManualMessageE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        repo_env = _load_repo_env()
        if not _setting("GEMINIBOT_E2E_MANUAL", repo_env):
            raise unittest.SkipTest("Set GEMINIBOT_E2E_MANUAL=1 to run manual Feishu message E2E tests.")

        cls.repo_env = repo_env
        cls.chat_id = _setting("GEMINIBOT_E2E_FEISHU_CHAT_ID", repo_env)
        cls.conversation_id = _setting("GEMINIBOT_E2E_CONVERSATION_ID", repo_env, cls.chat_id)
        cls.user_id = _setting("GEMINIBOT_E2E_FEISHU_USER_ID", repo_env, "manual-e2e-user")
        missing = [
            name
            for name, value in {
                "GEMINIBOT_E2E_FEISHU_CHAT_ID": cls.chat_id,
                "GEMINIBOT_E2E_CONVERSATION_ID": cls.conversation_id,
            }.items()
            if not value
        ]
        if missing:
            raise unittest.SkipTest(f"Missing manual E2E settings: {', '.join(missing)}")

        workspace_root = Path(
            _setting(
                "WORKSPACE_ROOT",
                repo_env,
                str(Path.home() / "geminibot" / "workspaces"),
            )
        ).expanduser()
        data_root = Path(
            _setting(
                "DATA_ROOT",
                repo_env,
                str(Path.home() / "geminibot" / "data"),
            )
        ).expanduser()

        cls.config = AppConfig(
            feishu_app_id=_setting("FEISHU_APP_ID", repo_env),
            feishu_app_secret=_setting("FEISHU_APP_SECRET", repo_env),
            gemini_api_key=_setting("GEMINI_API_KEY", repo_env),
            ai_provider=_setting("AI_PROVIDER", repo_env, "gemini"),
            gemini_cli_path=_setting("GEMINI_CLI_PATH", repo_env, "gemini"),
            claude_cli_path=_setting("CLAUDE_CLI_PATH", repo_env, "claude"),
            bot_name=_setting("BOT_NAME", repo_env, "GeminiBot"),
            default_timezone=_setting("DEFAULT_TIMEZONE", repo_env, "Asia/Shanghai"),
            workspace_root=workspace_root,
            data_root=data_root,
            poll_interval_seconds=int(_setting("POLL_INTERVAL_SECONDS", repo_env, "30")),
            recent_summary_days=int(_setting("RECENT_SUMMARY_DAYS", repo_env, "7")),
            card_footer_enabled=_setting("CARD_FOOTER_ENABLED", repo_env, "true").lower() == "true",
            log_level=_setting("LOG_LEVEL", repo_env, "INFO"),
        )
        cls.config.ensure_directories()

    def setUp(self) -> None:
        self.scheduler_store = SchedulerStore(self.config)
        self.created_task_ids: list[str] = []

    def tearDown(self) -> None:
        for task_id in self.created_task_ids:
            self.scheduler_store.delete_task(task_id)

    @property
    def workspace(self) -> Path:
        return self.config.workspace_root / self.conversation_id

    @property
    def today_log_file(self) -> Path:
        return self.workspace / "logs" / f"{date.today().isoformat()}.md"

    @property
    def today_summary_file(self) -> Path:
        return self.workspace / "summaries" / f"{date.today().isoformat()}.md"

    @property
    def memory_file(self) -> Path:
        return self.workspace / "MEMORY.md"

    @property
    def memory_metadata_file(self) -> Path:
        return self.workspace / "MEMORY.meta.json"

    def _read_json(self, path: Path) -> dict | list:
        return json.loads(path.read_text(encoding="utf-8"))

    def _read_tasks(self) -> list[dict]:
        tasks_file = self.config.data_root / "schedules.json"
        if not tasks_file.exists():
            return []
        return json.loads(tasks_file.read_text(encoding="utf-8"))

    def _tasks_for_chat(self) -> list[dict]:
        return [task for task in self._read_tasks() if task.get("chat_id") == self.chat_id]

    def _wait_until(self, predicate, *, timeout: float = MANUAL_WAIT_TIMEOUT_SECONDS, interval: float = MANUAL_POLL_INTERVAL_SECONDS) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return predicate()

    def _print_step(self, title: str, message_text: str, expected: str) -> None:
        print("\n" + "=" * 80)
        print(f"MANUAL E2E STEP: {title}")
        print(f"Chat ID: {self.chat_id}")
        print(f"Conversation ID: {self.conversation_id}")
        print("Send this exact message in Feishu:")
        print(message_text)
        print(f"Waiting for local verification: {expected}")
        print("=" * 80 + "\n")

    def _wait_for_manual_message(self, *, title: str, message_text: str, expected: str, predicate, timeout: float = MANUAL_WAIT_TIMEOUT_SECONDS) -> None:
        self._print_step(title, message_text, expected)
        self.assertTrue(self._wait_until(predicate, timeout=timeout), f"Timed out waiting for manual step: {title}")

    def test_manual_remember_command_persists_memory(self) -> None:
        note = f"MANUAL_E2E_REMEMBER_{uuid4().hex[:8]}"
        message_text = f"/remember {note}"

        def predicate() -> bool:
            if not (self.memory_file.exists() and self.memory_metadata_file.exists() and self.today_log_file.exists()):
                return False
            memory_text = self.memory_file.read_text(encoding="utf-8")
            metadata = self._read_json(self.memory_metadata_file)
            logs_text = self.today_log_file.read_text(encoding="utf-8")
            return (
                note in memory_text
                and any(
                    entry.get("content") == note and entry.get("source") == "remember"
                    for entry in metadata.get("sections", {}).get("User Preferences", [])
                )
                and f"**Q:** {message_text}" in logs_text
            )

        self._wait_for_manual_message(
            title="remember command",
            message_text=message_text,
            expected="memory note appears in MEMORY.md, metadata source=remember, and today's log contains the command",
            predicate=predicate,
        )

    def test_manual_schedule_once_command_creates_task(self) -> None:
        prompt = f"MANUAL_E2E_ONCE_{uuid4().hex[:8]}"
        message_text = f"/schedule once | 2099-12-31T09:00:00 | {prompt}"
        existing_ids = {task.get("id") for task in self._tasks_for_chat()}

        captured: dict[str, str] = {}

        def predicate() -> bool:
            for task in self._tasks_for_chat():
                if task.get("id") in existing_ids:
                    continue
                if task.get("prompt") != prompt:
                    continue
                if task.get("schedule_type") != "once":
                    continue
                captured["task_id"] = task["id"]
                return True
            return False

        self._wait_for_manual_message(
            title="schedule once command",
            message_text=message_text,
            expected="a new once task with the unique prompt is written to schedules.json",
            predicate=predicate,
        )
        self.created_task_ids.append(captured["task_id"])

    def test_manual_schedule_cron_command_creates_task(self) -> None:
        prompt = f"MANUAL_E2E_CRON_{uuid4().hex[:8]}"
        message_text = f"/schedule cron | 0 18 * * 5 | {prompt}"
        existing_ids = {task.get("id") for task in self._tasks_for_chat()}

        captured: dict[str, str] = {}

        def predicate() -> bool:
            for task in self._tasks_for_chat():
                if task.get("id") in existing_ids:
                    continue
                if task.get("prompt") != prompt:
                    continue
                if task.get("schedule_type") != "cron":
                    continue
                captured["task_id"] = task["id"]
                return True
            return False

        self._wait_for_manual_message(
            title="schedule cron command",
            message_text=message_text,
            expected="a new cron task with the unique prompt is written to schedules.json",
            predicate=predicate,
        )
        self.created_task_ids.append(captured["task_id"])

    def test_manual_tasks_command_logs_current_task_list(self) -> None:
        prompt = f"MANUAL_E2E_TASKS_{uuid4().hex[:8]}"
        task = self.scheduler_store.create_task(
            chat_id=self.chat_id,
            conversation_id=self.conversation_id,
            prompt=prompt,
            schedule_type="once",
            schedule_value="2099-12-31T09:00:00",
            created_by=self.user_id,
            timezone=self.config.default_timezone,
        )
        self.created_task_ids.append(task["id"])
        message_text = "/tasks"

        def predicate() -> bool:
            if not self.today_log_file.exists():
                return False
            logs_text = self.today_log_file.read_text(encoding="utf-8")
            return f"**Q:** {message_text}" in logs_text and prompt in logs_text and task["id"] in logs_text

        self._wait_for_manual_message(
            title="tasks command",
            message_text=message_text,
            expected="today's log records a /tasks reply containing the seeded task id and prompt",
            predicate=predicate,
        )

    def test_manual_delete_task_command_removes_task(self) -> None:
        task = self.scheduler_store.create_task(
            chat_id=self.chat_id,
            conversation_id=self.conversation_id,
            prompt=f"MANUAL_E2E_DELETE_{uuid4().hex[:8]}",
            schedule_type="once",
            schedule_value="2099-12-31T09:00:00",
            created_by=self.user_id,
            timezone=self.config.default_timezone,
        )
        message_text = f"/delete-task {task['id']}"

        def predicate() -> bool:
            tasks = self._tasks_for_chat()
            if any(current.get("id") == task["id"] for current in tasks):
                return False
            if not self.today_log_file.exists():
                return False
            logs_text = self.today_log_file.read_text(encoding="utf-8")
            return f"**Q:** {message_text}" in logs_text and f"Deleted task: {task['id']}" in logs_text

        self._wait_for_manual_message(
            title="delete task command",
            message_text=message_text,
            expected="the seeded task disappears from schedules.json and today's log records the deletion reply",
            predicate=predicate,
        )

    def test_manual_clear_command_generates_summary(self) -> None:
        token = f"MANUAL_E2E_CLEAR_{uuid4().hex[:8]}"
        preload_text = f"Please acknowledge this tracking token exactly once: {token}"
        self._wait_for_manual_message(
            title="preload conversation before clear",
            message_text=preload_text,
            expected="today's log contains the unique preload token before consolidation runs",
            predicate=lambda: self.today_log_file.exists() and token in self.today_log_file.read_text(encoding="utf-8"),
        )

        previous_summary_mtime = self.today_summary_file.stat().st_mtime if self.today_summary_file.exists() else 0.0
        previous_summary_sources = 0
        if self.memory_metadata_file.exists():
            metadata = self._read_json(self.memory_metadata_file)
            previous_summary_sources = sum(
                1
                for entries in metadata.get("sections", {}).values()
                for entry in entries
                if str(entry.get("source", "")).startswith("summary:")
            )

        message_text = "/clear"

        def predicate() -> bool:
            if not self.today_summary_file.exists():
                return False
            summary_text = self.today_summary_file.read_text(encoding="utf-8")
            if "### Semantic Summary" not in summary_text or "### Potential Long-Term Notes" not in summary_text:
                return False
            if self.today_summary_file.stat().st_mtime > previous_summary_mtime:
                return True
            if self.memory_metadata_file.exists():
                metadata = self._read_json(self.memory_metadata_file)
                current_summary_sources = sum(
                    1
                    for entries in metadata.get("sections", {}).values()
                    for entry in entries
                    if str(entry.get("source", "")).startswith("summary:")
                )
                if current_summary_sources > previous_summary_sources:
                    return True
            return False

        self._wait_for_manual_message(
            title="clear command",
            message_text=message_text,
            expected="today's summary file is regenerated or summary-derived metadata increases",
            predicate=predicate,
            timeout=240.0,
        )


def build_manual_test_suite() -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    return unittest.TestSuite(
        loader.loadTestsFromName(
            f"{ManualMessageE2ETests.__module__}.{ManualMessageE2ETests.__name__}.{test_name}"
        )
        for test_name, _ in MANUAL_TEST_SEQUENCE
    )


def print_manual_test_plan(stream=None) -> None:
    output = stream or sys.stdout
    print("Manual Feishu message E2E sequence:", file=output)
    for index, (_, description) in enumerate(MANUAL_TEST_SEQUENCE, start=1):
        print(f"  {index}. {description}", file=output)


def run_manual_test_suite(*, verbosity: int = 2) -> unittest.result.TestResult:
    print_manual_test_plan()
    print()
    runner = unittest.TextTestRunner(verbosity=verbosity)
    return runner.run(build_manual_test_suite())


def load_tests(loader, standard_tests, pattern) -> unittest.TestSuite:
    print_manual_test_plan()
    print()
    return build_manual_test_suite()


if __name__ == "__main__":
    result = run_manual_test_suite()
    raise SystemExit(0 if result.wasSuccessful() else 1)
