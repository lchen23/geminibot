from __future__ import annotations

from pathlib import Path
from typing import Any

from app.utils.state import JsonDictState


class SessionStore:
    def __init__(self, file_path: Path) -> None:
        self.state = JsonDictState(file_path)

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        return self.state.read().get(conversation_id)

    def set(self, conversation_id: str, value: dict[str, Any]) -> None:
        data = self.state.read()
        data[conversation_id] = value
        self.state.write(data)

    def delete(self, conversation_id: str) -> None:
        data = self.state.read()
        data.pop(conversation_id, None)
        self.state.write(data)
