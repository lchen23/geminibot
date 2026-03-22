from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonListState:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def read(self) -> list[Any]:
        if not self.file_path.exists():
            return []
        return json.loads(self.file_path.read_text(encoding="utf-8"))

    def write(self, value: list[Any]) -> None:
        _write_json(self.file_path, value)


class JsonDictState:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def read(self) -> dict[str, Any]:
        if not self.file_path.exists():
            return {}
        return json.loads(self.file_path.read_text(encoding="utf-8"))

    def write(self, value: dict[str, Any]) -> None:
        _write_json(self.file_path, value)


def _write_json(file_path: Path, value: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(file_path)
