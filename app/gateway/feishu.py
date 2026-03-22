from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app.config import AppConfig
from app.dispatcher import Dispatcher, IncomingMessage
from app.utils.state import JsonListState

logger = logging.getLogger(__name__)


class FeishuGateway:
    def __init__(self, config: AppConfig, dispatcher: Dispatcher) -> None:
        self.config = config
        self.dispatcher = dispatcher
        self.dedup_store = JsonListState(config.data_root / "dedup.json")
        self.unsent_store = config.data_root / "unsent_messages.json"

    def start(self) -> None:
        logger.info("FeishuGateway started in local mode. Use handle_text_message() or CLI simulation to feed messages.")

    def handle_text_message(
        self,
        *,
        message_id: str,
        chat_id: str,
        user_id: str,
        conversation_id: str,
        text: str,
    ) -> dict | None:
        seen = set(self.dedup_store.read())
        if message_id in seen:
            logger.info("Skipping duplicate message_id=%s", message_id)
            return None

        seen.add(message_id)
        self.dedup_store.write(sorted(seen)[-1000:])

        request = IncomingMessage(
            message_id=message_id,
            chat_id=chat_id,
            user_id=user_id,
            conversation_id=conversation_id,
            text=text,
            sent_at=datetime.now(timezone.utc).isoformat(),
        )
        response = self.dispatcher.handle(request)
        logger.info("Prepared response for chat_id=%s", chat_id)
        return response

    def deliver(self, chat_id: str, payload: dict) -> None:
        message = {"chat_id": chat_id, "payload": payload, "sent_at": datetime.now(timezone.utc).isoformat()}
        logger.info("Delivering message to chat_id=%s payload=%s", chat_id, json.dumps(payload, ensure_ascii=False))
        self._append_unsent(message)

    def _append_unsent(self, message: dict) -> None:
        existing: list[dict] = []
        if self.unsent_store.exists():
            existing = json.loads(self.unsent_store.read_text(encoding="utf-8"))
        existing.append(message)
        self.unsent_store.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
