from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional runtime fallback
    def load_dotenv() -> bool:
        return False


@dataclass(slots=True)
class StartupCheckResult:
    warnings: list[str]


@dataclass(slots=True)
class AppConfig:
    feishu_app_id: str
    feishu_app_secret: str
    gemini_api_key: str
    gemini_cli_path: str
    bot_name: str
    default_timezone: str
    workspace_root: Path
    data_root: Path
    poll_interval_seconds: int
    recent_summary_days: int
    card_footer_enabled: bool
    log_level: str

    @classmethod
    def load(cls) -> "AppConfig":
        load_dotenv()

        workspace_root = Path(os.getenv("WORKSPACE_ROOT", str(Path.home() / "geminibot" / "workspaces"))).expanduser()
        data_root = Path(os.getenv("DATA_ROOT", str(Path.home() / "geminibot" / "data"))).expanduser()

        config = cls(
            feishu_app_id=os.getenv("FEISHU_APP_ID", ""),
            feishu_app_secret=os.getenv("FEISHU_APP_SECRET", ""),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_cli_path=os.getenv("GEMINI_CLI_PATH", "gemini"),
            bot_name=os.getenv("BOT_NAME", "GeminiBot"),
            default_timezone=os.getenv("DEFAULT_TIMEZONE", "Asia/Shanghai"),
            workspace_root=workspace_root,
            data_root=data_root,
            poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "30")),
            recent_summary_days=int(os.getenv("RECENT_SUMMARY_DAYS", "7")),
            card_footer_enabled=os.getenv("CARD_FOOTER_ENABLED", "true").lower() == "true",
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
        config.ensure_directories()
        return config

    def run_startup_checks(self) -> StartupCheckResult:
        if not self.feishu_app_id or not self.feishu_app_secret:
            raise ValueError("Missing required Feishu configuration: FEISHU_APP_ID and FEISHU_APP_SECRET are required.")
        if not self.gemini_cli_path.strip():
            raise ValueError("Missing required Gemini configuration: GEMINI_CLI_PATH is required.")
        if shutil.which(self.gemini_cli_path) is None:
            raise ValueError(f"Gemini CLI was not found on PATH: {self.gemini_cli_path}")
        if not self.workspace_root.exists() or not self.workspace_root.is_dir():
            raise ValueError(f"Workspace root is not available: {self.workspace_root}")
        if not self.data_root.exists() or not self.data_root.is_dir():
            raise ValueError(f"Data root is not available: {self.data_root}")
        try:
            ZoneInfo(self.default_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Default timezone is not supported: {self.default_timezone}") from exc

        warnings: list[str] = []
        if not self.gemini_api_key:
            warnings.append("GEMINI_API_KEY is not set; Gemini CLI must already be authenticated via its own local session.")
        return StartupCheckResult(warnings=warnings)

    def ensure_directories(self) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        (self.data_root / "schedules.json").write_text("[]\n", encoding="utf-8") if not (self.data_root / "schedules.json").exists() else None
        (self.data_root / "sessions.json").write_text("{}\n", encoding="utf-8") if not (self.data_root / "sessions.json").exists() else None
        (self.data_root / "dedup.json").write_text("[]\n", encoding="utf-8") if not (self.data_root / "dedup.json").exists() else None
