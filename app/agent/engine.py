from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.agent.session_store import SessionStore
from app.agent.workspace import WorkspaceManager
from app.config import AppConfig
from app.memory.store import MemoryStore


@dataclass
class AgentRequest:
    conversation_id: str
    chat_id: str
    user_id: str
    text: str
    source: str


@dataclass
class AgentResult:
    text: str
    raw_output: str
    session_id: str | None = None
    model: str | None = None
    events: list[dict] | None = None


@dataclass
class AgentStreamResult:
    result: AgentResult
    events: list[dict]


class GeminiAgentEngine:
    def __init__(self, config: AppConfig, memory_store: MemoryStore) -> None:
        self.config = config
        self.memory_store = memory_store
        self.workspace_manager = WorkspaceManager(config)
        self.session_store = SessionStore(config.data_root / "sessions.json")

    def run(self, request: AgentRequest) -> AgentResult:
        return self.run_stream(request).result

    def run_stream(
        self,
        request: AgentRequest,
        on_event: Callable[[dict], None] | None = None,
    ) -> "AgentStreamResult":
        workspace = self.workspace_manager.ensure_workspace(request.conversation_id)
        session = self.session_store.get(request.conversation_id)
        system_prompt = self._build_system_prompt(workspace)
        self._write_gemini_context_file(workspace, system_prompt)
        env = self._build_environment(request, workspace)

        try:
            completed, parsed = self._invoke(request, session, workspace, env, on_event=on_event)
        except FileNotFoundError:
            result = AgentResult(
                text="Gemini CLI was not found. Please install it or configure GEMINI_CLI_PATH.",
                raw_output="",
                events=[],
            )
            return AgentStreamResult(result=result, events=[])

        if self._should_retry_without_resume(completed, parsed, session):
            self.session_store.delete(request.conversation_id)
            completed, parsed = self._invoke(request, None, workspace, env, on_event=on_event)

        if parsed.session_id:
            self.session_store.set(
                request.conversation_id,
                {
                    "session_id": parsed.session_id,
                    "resume": "latest",
                },
            )

        if completed.returncode != 0 and not parsed.text:
            parsed = AgentResult(
                text=completed.stderr.strip() or "Gemini CLI exited with a non-zero status.",
                raw_output=self._join_output(completed.stdout.strip(), completed.stderr.strip()),
                session_id=parsed.session_id,
                model=parsed.model,
                events=parsed.events,
            )
        return AgentStreamResult(result=parsed, events=parsed.events or [])

    def clear_conversation(self, conversation_id: str) -> None:
        self.session_store.delete(conversation_id)

    def _build_system_prompt(self, workspace: Path) -> str:
        parts: list[str] = []
        for filename in ["SOUL.md", "IDENTITY.md", "USER.md", "AGENT.md", "MEMORY.md"]:
            path = workspace / filename
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"## {filename}\n{content}")

        tool_guide = self._read_tool_guide(workspace)
        if tool_guide:
            parts.append(f"## TOOL_BRIDGE.md\n{tool_guide}")

        recent = self.memory_store.read_recent_summaries(workspace=workspace, days=self.config.recent_summary_days)
        if recent:
            parts.append(f"## Recent Summaries\n{recent}")
        return "\n\n".join(parts)

    def _write_gemini_context_file(self, workspace: Path, system_prompt: str) -> None:
        context_file = workspace / "GEMINI.md"
        if system_prompt:
            context_file.write_text(system_prompt.rstrip() + "\n", encoding="utf-8")
        elif context_file.exists():
            context_file.unlink()

    def _invoke(
        self,
        request: AgentRequest,
        session: dict | None,
        workspace: Path,
        env: dict[str, str],
        on_event: Callable[[dict], None] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], AgentResult]:
        command = self._build_command(request, session)
        if self._output_format() == "stream-json":
            completed = self._invoke_streaming(command, workspace, env, on_event=on_event)
            return completed, self._parse_output(completed.stdout.strip(), completed.stderr.strip())

        completed = subprocess.run(
            command,
            cwd=workspace,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        return completed, self._parse_output(stdout, stderr, on_event=on_event)

    def _invoke_streaming(
        self,
        command: list[str],
        workspace: Path,
        env: dict[str, str],
        on_event: Callable[[dict], None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1,
        )
        stdout_lines: list[str] = []
        stderr_text = ""

        assert process.stdout is not None
        for line in process.stdout:
            stdout_lines.append(line)
            payload = line.strip()
            if not payload or on_event is None:
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                on_event(event)

        if process.stderr is not None:
            stderr_text = process.stderr.read()
        returncode = process.wait()
        return subprocess.CompletedProcess(
            args=command,
            returncode=returncode,
            stdout="".join(stdout_lines),
            stderr=stderr_text,
        )

    def _build_command(self, request: AgentRequest, session: dict | None) -> list[str]:
        output_format = self._output_format()
        command = [
            self.config.gemini_cli_path,
            "-p",
            request.text,
            "--output-format",
            output_format,
        ]
        if output_format == "stream-json":
            command.append("--verbose")
        resume_value = self._resume_value(session)
        if resume_value:
            command.extend(["--resume", resume_value])
        return command

    def _resume_value(self, session: dict | None) -> str | None:
        if not session:
            return None

        cli_name = Path(self.config.gemini_cli_path).name.lower()
        if cli_name == "claude":
            session_id = str(session.get("session_id") or "").strip()
            return session_id if self._is_uuid(session_id) else None

        resume_value = str(session.get("resume") or "").strip()
        return resume_value or None

    def _output_format(self) -> str:
        cli_name = Path(self.config.gemini_cli_path).name.lower()
        return "stream-json" if cli_name == "claude" else "json"

    def _is_uuid(self, value: str) -> bool:
        try:
            UUID(value)
        except ValueError:
            return False
        return True

    def _should_retry_without_resume(
        self,
        completed: subprocess.CompletedProcess[str],
        parsed: AgentResult,
        session: dict | None,
    ) -> bool:
        if not session:
            return False
        if completed.returncode == 0:
            return False

        cli_name = Path(self.config.gemini_cli_path).name.lower()
        if cli_name != "claude":
            return False

        message = parsed.text.lower()
        return "no conversation found with session id" in message

    def _build_environment(self, request: AgentRequest, workspace: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "GEMINIBOT_PROJECT_ROOT": str(self.workspace_manager.project_root),
                "GEMINIBOT_WORKSPACE": str(workspace),
                "GEMINIBOT_CONVERSATION_ID": request.conversation_id,
                "GEMINIBOT_CHAT_ID": request.chat_id,
                "GEMINIBOT_USER_ID": request.user_id,
                "GEMINIBOT_TIMEZONE": self.config.default_timezone,
            }
        )
        return env

    def _read_tool_guide(self, workspace: Path) -> str:
        guide_path = workspace / "tools" / "README.md"
        if not guide_path.exists():
            return ""
        return guide_path.read_text(encoding="utf-8").strip()

    def _parse_output(
        self,
        stdout: str,
        stderr: str,
        on_event: Callable[[dict], None] | None = None,
    ) -> AgentResult:
        raw_output = self._join_output(stdout, stderr)
        if not stdout:
            return AgentResult(text=stderr or "Gemini CLI returned no output.", raw_output=raw_output, events=[])

        events = self._parse_json_events(stdout)
        if events:
            return self._result_from_events(events, stderr, raw_output, on_event=on_event)

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return AgentResult(text=stdout, raw_output=raw_output, events=[])

        error = data.get("error")
        if isinstance(error, dict):
            error_message = error.get("message") or json.dumps(error, ensure_ascii=False)
            return AgentResult(
                text=error_message,
                raw_output=raw_output,
                session_id=data.get("session_id"),
                model=data.get("model"),
                events=[data],
            )

        response = data.get("response")
        result_text = data.get("result") if isinstance(data.get("result"), str) else None
        return AgentResult(
            text=response or result_text or stderr or json.dumps(data, ensure_ascii=False),
            raw_output=raw_output,
            session_id=data.get("session_id"),
            model=data.get("model"),
            events=[data],
        )

    def _parse_json_events(self, stdout: str) -> list[dict]:
        events: list[dict] = []
        for line in stdout.splitlines():
            payload = line.strip()
            if not payload:
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                return []
            if not isinstance(data, dict):
                return []
            events.append(data)
        return events

    def _result_from_events(
        self,
        events: list[dict],
        stderr: str,
        raw_output: str,
        on_event: Callable[[dict], None] | None = None,
    ) -> AgentResult:
        text_parts: list[str] = []
        session_id: str | None = None
        model: str | None = None
        error_message: str | None = None

        for event in events:
            if on_event is not None:
                on_event(event)
            if not session_id and event.get("session_id"):
                session_id = event.get("session_id")
            if not model and event.get("model"):
                model = event.get("model")

            message = event.get("message")
            if not model and isinstance(message, dict) and isinstance(message.get("model"), str):
                model = message["model"]

            chunk = self._event_text_chunk(event)
            if chunk:
                text_parts.append(chunk)

            if event.get("type") == "result":
                result_text = event.get("result")
                if isinstance(result_text, str) and not text_parts:
                    text_parts.append(result_text)
                if event.get("is_error") and isinstance(result_text, str):
                    error_message = result_text

        text = "".join(text_parts).strip() or error_message or stderr or json.dumps(events[-1], ensure_ascii=False)
        return AgentResult(
            text=text,
            raw_output=raw_output,
            session_id=session_id,
            model=model,
            events=events,
        )

    def _event_text_chunk(self, event: dict) -> str:
        event_type = event.get("type")
        if event_type == "assistant" and isinstance(event.get("message"), dict):
            text_parts: list[str] = []
            for block in event["message"].get("content", []):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    text_parts.append(block["text"])
            return "".join(text_parts)

        if event_type == "content_block_delta":
            delta = event.get("delta")
            if isinstance(delta, dict) and delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                return delta["text"]

        if event_type == "content_block_start":
            content_block = event.get("content_block")
            if isinstance(content_block, dict) and content_block.get("type") == "text" and isinstance(content_block.get("text"), str):
                return content_block["text"]

        return ""

    def _join_output(self, stdout: str, stderr: str) -> str:
        outputs = [part for part in [stdout, stderr] if part]
        return "\n".join(outputs)
