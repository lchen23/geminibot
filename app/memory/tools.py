from __future__ import annotations

from app.config import AppConfig
from app.memory.store import MemoryStore


class MemoryTools:
    def __init__(self, config: AppConfig) -> None:
        self.store = MemoryStore(config)

    def memory_search(self, conversation_id: str, query: str, limit: int = 10) -> list[str]:
        return self.store.search(conversation_id=conversation_id, query=query, limit=limit)

    def memory_list_by_date(self, conversation_id: str, start_date: str, end_date: str) -> list[str]:
        return self.store.list_by_date(
            conversation_id=conversation_id,
            start_date=start_date,
            end_date=end_date,
        )

    def memory_save(self, conversation_id: str, content: str) -> str:
        self.store.save_memory_note(conversation_id, content)
        return content
