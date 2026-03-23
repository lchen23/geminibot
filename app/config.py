from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional runtime fallback
    def load_dotenv() -> bool:
        return False


@dataclass
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

    def ensure_directories(self) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        (self.data_root / "schedules.json").write_text("[]\n", encoding="utf-8") if not (self.data_root / "schedules.json").exists() else None
        (self.data_root / "sessions.json").write_text("{}\n", encoding="utf-8") if not (self.data_root / "sessions.json").exists() else None
        (self.data_root / "dedup.json").write_text("[]\n", encoding="utf-8") if not (self.data_root / "dedup.json").exists() else None
