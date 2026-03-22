from __future__ import annotations

import shutil
from pathlib import Path

from app.config import AppConfig


TEMPLATE_FILES = ["SOUL.md", "IDENTITY.md", "USER.md", "AGENT.md", "MEMORY.md"]


class WorkspaceManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.project_root = config.workspace_root.parent
        self.templates_root = self.project_root / "templates"

    def ensure_workspace(self, conversation_id: str) -> Path:
        workspace_dir = self.config.workspace_root / conversation_id
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "logs").mkdir(exist_ok=True)
        (workspace_dir / "summaries").mkdir(exist_ok=True)
        (workspace_dir / "tools").mkdir(exist_ok=True)

        for filename in TEMPLATE_FILES:
            target = workspace_dir / filename
            source = self.templates_root / filename
            if not target.exists() and source.exists():
                shutil.copyfile(source, target)

        return workspace_dir
