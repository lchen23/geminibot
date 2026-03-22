from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib import error, request

try:
    from lark_oapi import EventDispatcherHandler, LogLevel, ws
except ModuleNotFoundError:  # pragma: no cover - optional runtime fallback
    EventDispatcherHandler = None
    ws = None
    LogLevel = None

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
        self._tenant_access_token: str | None = None
        self._tenant_token_expires_at = 0.0
        self._ws_client: Any | None = None
        self._ws_thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._has_credentials():
            logger.info("FeishuGateway started in local mode. Use handle_text_message() or CLI simulation to feed messages.")
            return

        self._get_tenant_access_token(force_refresh=True)
        self._start_websocket_client()
        logger.info("FeishuGateway initialized Feishu client for app_id=%s", self.config.feishu_app_id)

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

        request_message = IncomingMessage(
            message_id=message_id,
            chat_id=chat_id,
            user_id=user_id,
            conversation_id=conversation_id,
            text=text,
            sent_at=datetime.now(timezone.utc).isoformat(),
        )
        response = self.dispatcher.handle(request_message)
        logger.info("Prepared response for chat_id=%s", chat_id)
        return response

    def deliver(self, chat_id: str, payload: dict) -> None:
        message = {"chat_id": chat_id, "payload": payload, "sent_at": datetime.now(timezone.utc).isoformat()}
        logger.info("Delivering message to chat_id=%s payload=%s", chat_id, json.dumps(payload, ensure_ascii=False))

        try:
            if self._has_credentials():
                self._send_card_message(chat_id, payload)
                return
        except RuntimeError as exc:
            logger.warning("Feishu delivery failed for chat_id=%s: %s", chat_id, exc)
            message["delivery_error"] = str(exc)

        self._append_unsent(message)

    def _has_credentials(self) -> bool:
        return bool(self.config.feishu_app_id and self.config.feishu_app_secret)

    def _get_tenant_access_token(self, *, force_refresh: bool = False) -> str:
        now = time.time()
        if not force_refresh and self._tenant_access_token and now < self._tenant_token_expires_at - 60:
            return self._tenant_access_token

        payload = self._post_json(
            url="https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            body={
                "app_id": self.config.feishu_app_id,
                "app_secret": self.config.feishu_app_secret,
            },
        )
        if payload.get("code") != 0:
            raise RuntimeError(f"Feishu auth failed: {payload.get('msg', 'unknown error')}")

        token = payload.get("tenant_access_token")
        if not token:
            raise RuntimeError("Feishu auth failed: tenant_access_token missing in response")

        self._tenant_access_token = token
        self._tenant_token_expires_at = now + int(payload.get("expire", 7200))
        return token

    def _send_card_message(self, chat_id: str, payload: dict) -> None:
        token = self._get_tenant_access_token()
        response_payload = self._post_json(
            url="https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            body={
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(payload, ensure_ascii=False),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        if response_payload.get("code") != 0:
            raise RuntimeError(f"Feishu send failed: {response_payload.get('msg', 'unknown error')}")

    def _start_websocket_client(self) -> None:
        if self._ws_thread and self._ws_thread.is_alive():
            return
        if ws is None or EventDispatcherHandler is None or LogLevel is None:
            logger.warning("lark-oapi SDK is not installed; FeishuGateway will run without WebSocket subscription.")
            return

        event_handler = self._build_event_handler()
        self._ws_client = ws.Client(
            self.config.feishu_app_id,
            self.config.feishu_app_secret,
            event_handler=event_handler,
            log_level=LogLevel.INFO,
        )
        self._ws_thread = threading.Thread(target=self._run_websocket_client, daemon=True, name="feishu-websocket")
        self._ws_thread.start()
        logger.info("Feishu WebSocket client thread started.")

    def _run_websocket_client(self) -> None:
        if self._ws_client is None:
            return
        try:
            self._ws_client.start()
        except Exception:  # pragma: no cover - depends on external SDK/runtime
            logger.exception("Feishu WebSocket client stopped unexpectedly.")

    def _build_event_handler(self) -> Any:
        handler = EventDispatcherHandler.builder("", "")
        register = getattr(handler, "register_p2_im_message_receive_v1", None)
        if callable(register):
            handler = register(self._handle_ws_message_receive)
            return handler.build()
        raise RuntimeError("Installed lark-oapi SDK does not support register_p2_im_message_receive_v1")

    def _handle_ws_message_receive(self, data: Any) -> None:
        event = getattr(data, "event", None)
        if event is None:
            logger.warning("Received Feishu event without event payload: %r", data)
            return

        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        if message is None or sender is None:
            logger.warning("Received Feishu message event with missing sender/message: %r", data)
            return

        text = self._extract_text_content(getattr(message, "content", ""))
        if not text:
            logger.info("Skipping Feishu event without text content.")
            return

        response = self.handle_text_message(
            message_id=getattr(message, "message_id", ""),
            chat_id=getattr(message, "chat_id", ""),
            user_id=self._extract_user_id(sender),
            conversation_id=getattr(message, "chat_id", ""),
            text=text,
        )
        if response is not None:
            self.deliver(getattr(message, "chat_id", ""), response)

    def _extract_text_content(self, raw_content: str) -> str:
        if not raw_content:
            return ""
        try:
            payload = json.loads(raw_content)
        except json.JSONDecodeError:
            return raw_content.strip()
        text = payload.get("text", "")
        return text.strip() if isinstance(text, str) else ""

    def _extract_user_id(self, sender: Any) -> str:
        sender_id = getattr(sender, "sender_id", None)
        if sender_id is None:
            return ""
        for field_name in ("open_id", "user_id", "union_id"):
            value = getattr(sender_id, field_name, None)
            if value:
                return str(value)
        return ""

    def _post_json(self, *, url: str, body: dict, headers: dict[str, str] | None = None) -> dict:
        request_headers = {
            "Content-Type": "application/json; charset=utf-8",
        }
        if headers:
            request_headers.update(headers)

        http_request = request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=10) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {raw}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"network error: {exc.reason}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON response: {raw}") from exc

    def _append_unsent(self, message: dict) -> None:
        existing: list[dict] = []
        if self.unsent_store.exists():
            existing = json.loads(self.unsent_store.read_text(encoding="utf-8"))
        existing.append(message)
        self.unsent_store.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
