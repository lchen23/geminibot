from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - optional runtime fallback
    def load_dotenv(dotenv_path: str | Path | None = None) -> bool:
        return False


@dataclass(slots=True)
class StartupCheckResult:
    warnings: list[str]


@dataclass(slots=True)
class AppConfig:
    feishu_app_id: str
    feishu_app_secret: str
    gemini_api_key: str
    ai_provider: str
    gemini_cli_path: str
    claude_cli_path: str
    gemini_approval_mode: str
    claude_permission_mode: str
    bot_name: str
    default_timezone: str
    app_root: Path
    workspace_root: Path = field(init=False)
    data_root: Path = field(init=False)
    poll_interval_seconds: int = 30
    recent_summary_days: int = 7
    card_footer_enabled: bool = True
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        self.app_root = self.app_root.expanduser()
        self.workspace_root = self.app_root / "workspaces"
        self.data_root = self.app_root / "data"

    @classmethod
    def load(cls) -> "AppConfig":
        repo_root = Path(__file__).resolve().parent.parent
        load_dotenv(repo_root / ".env")

        app_root = Path(os.getenv("APP_ROOT", str(repo_root))).expanduser()

        config = cls(
            feishu_app_id=os.getenv("FEISHU_APP_ID", ""),
            feishu_app_secret=os.getenv("FEISHU_APP_SECRET", ""),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            ai_provider=os.getenv("AI_PROVIDER", "gemini").strip().lower() or "gemini",
            gemini_cli_path=os.getenv("GEMINI_CLI_PATH", "gemini"),
            claude_cli_path=os.getenv("CLAUDE_CLI_PATH", "claude"),
            gemini_approval_mode=os.getenv("GEMINI_APPROVAL_MODE", "default").strip().lower() or "default",
            claude_permission_mode=os.getenv("CLAUDE_PERMISSION_MODE", "default").strip() or "default",
            bot_name=os.getenv("BOT_NAME", "GeminiBot"),
            default_timezone=os.getenv("DEFAULT_TIMEZONE", "Asia/Shanghai"),
            app_root=app_root,
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
        if self.ai_provider not in {"gemini", "claude"}:
            raise ValueError(f"Unsupported AI_PROVIDER: {self.ai_provider}")
        if self.gemini_approval_mode not in {"default", "auto_edit", "plan", "yolo"}:
            raise ValueError(f"Unsupported GEMINI_APPROVAL_MODE: {self.gemini_approval_mode}")
        if self.claude_permission_mode not in {"acceptEdits", "auto", "bypassPermissions", "default", "dontAsk", "plan"}:
            raise ValueError(f"Unsupported CLAUDE_PERMISSION_MODE: {self.claude_permission_mode}")

        cli_path = self.selected_cli_path
        if not cli_path.strip():
            raise ValueError(f"Missing required CLI configuration for provider {self.ai_provider}: set the matching CLI path.")
        if shutil.which(cli_path) is None:
            raise ValueError(f"Configured {self.ai_provider} CLI was not found on PATH: {cli_path}")
        if not self.workspace_root.exists() or not self.workspace_root.is_dir():
            raise ValueError(f"Workspace root is not available: {self.workspace_root}")
        if not self.data_root.exists() or not self.data_root.is_dir():
            raise ValueError(f"Data root is not available: {self.data_root}")
        try:
            ZoneInfo(self.default_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Default timezone is not supported: {self.default_timezone}") from exc

        warnings: list[str] = []
        if self.ai_provider == "gemini" and not self.gemini_api_key:
            warnings.append("GEMINI_API_KEY is not set; Gemini CLI must already be authenticated via its own local session.")
        return StartupCheckResult(warnings=warnings)

    @property
    def selected_cli_path(self) -> str:
        if self.ai_provider == "claude":
            return self.claude_cli_path
        return self.gemini_cli_path

    @property
    def context_filename(self) -> str:
        if self.ai_provider == "claude":
            return "CLAUDE.md"
        return "GEMINI.md"

    def ensure_directories(self) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        (self.data_root / "schedules.json").write_text("[]\n", encoding="utf-8") if not (self.data_root / "schedules.json").exists() else None
        (self.data_root / "sessions.json").write_text("{}\n", encoding="utf-8") if not (self.data_root / "sessions.json").exists() else None
        (self.data_root / "dedup.json").write_text("[]\n", encoding="utf-8") if not (self.data_root / "dedup.json").exists() else None
