from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import ClassVar, Iterator

from app.agent.session_store import SessionStore
from app.agent.workspace import WorkspaceManager
from app.config import AppConfig
from app.memory.store import MemoryStore


@dataclass(slots=True)
class AgentRequest:
    conversation_id: str
    chat_id: str
    user_id: str
    text: str
    source: str


@dataclass(slots=True)
class AgentResult:
    text: str
    raw_output: str
    session_id: str | None = None
    model: str | None = None


@dataclass(slots=True)
class AgentStreamEvent:
    delta: str = ""
    text: str = ""
    done: bool = False
    session_id: str | None = None
    model: str | None = None
    raw_event: str | None = None
    error: str | None = None


@dataclass(slots=True)
class PromptSnapshot:
    text: str
    signature: tuple[tuple[str, bool, int, int], ...]


class GeminiAgentEngine:
    _prompt_snapshot_cache: ClassVar[dict[tuple[str, int], PromptSnapshot]] = {}
    _prompt_snapshot_lock: ClassVar[Lock] = Lock()
    def __init__(self, config: AppConfig, memory_store: MemoryStore) -> None:
        self.config = config
        self.memory_store = memory_store
        self.workspace_manager = WorkspaceManager(config)
        self.session_store = SessionStore(config.data_root / "sessions.json")

    def run(self, request: AgentRequest) -> AgentResult:
        workspace = self.workspace_manager.ensure_workspace(request.conversation_id)
        session = self._load_session(request.conversation_id)
        system_prompt = self._build_system_prompt(workspace)
        self._write_context_file(workspace, system_prompt)
        command = self._build_command(request, session)
        env = self._build_environment(request, workspace)

        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        except FileNotFoundError:
            return AgentResult(
                text=f"{self._provider_label()} CLI was not found. Please install it or configure the matching CLI path.",
                raw_output="",
            )

        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        parsed = self._parse_output(stdout, stderr)

        if parsed.session_id:
            self._save_session(request.conversation_id, parsed.session_id)

        if completed.returncode != 0 and not parsed.text:
            return AgentResult(
                text=stderr or f"{self._provider_label()} CLI exited with a non-zero status.",
                raw_output=self._join_output(stdout, stderr),
                session_id=parsed.session_id,
                model=parsed.model,
            )
        return parsed

    def stream(self, request: AgentRequest) -> Iterator[AgentStreamEvent]:
        workspace = self.workspace_manager.ensure_workspace(request.conversation_id)
        session = self._load_session(request.conversation_id)
        system_prompt = self._build_system_prompt(workspace)
        self._write_context_file(workspace, system_prompt)
        command = self._build_command(request, session, output_format=self._stream_output_format())
        env = self._build_environment(request, workspace)

        try:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
        except FileNotFoundError:
            yield AgentStreamEvent(
                text=f"{self._provider_label()} CLI was not found. Please install it or configure the matching CLI path.",
                done=True,
                error="cli_not_found",
            )
            return

        assert process.stdout is not None
        assert process.stderr is not None

        full_text = ""
        session_id: str | None = None
        model: str | None = None
        raw_events: list[str] = []

        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                raw_events.append(line)
                event = self._parse_stream_event(line)
                if event.session_id:
                    session_id = event.session_id
                if event.model:
                    model = event.model
                if event.delta:
                    full_text += event.delta
                    yield AgentStreamEvent(
                        delta=event.delta,
                        text=full_text,
                        session_id=session_id,
                        model=model,
                        raw_event=line,
                    )
                if event.done:
                    break
        finally:
            returncode = process.wait()
            stderr = process.stderr.read().strip()

        if session_id:
            self._save_session(request.conversation_id, session_id)

        if returncode != 0 and not full_text:
            yield AgentStreamEvent(
                text=stderr or f"{self._provider_label()} CLI exited with a non-zero status.",
                done=True,
                session_id=session_id,
                model=model,
                raw_event=self._join_output("\n".join(raw_events), stderr),
                error="non_zero_exit",
            )
            return

        if not full_text:
            fallback = self._parse_output("\n".join(raw_events), stderr)
            yield AgentStreamEvent(
                text=fallback.text,
                done=True,
                session_id=fallback.session_id or session_id,
                model=fallback.model or model,
                raw_event=fallback.raw_output,
                error=None if fallback.text else "empty_output",
            )
            return

        yield AgentStreamEvent(
            text=full_text,
            done=True,
            session_id=session_id,
            model=model,
            raw_event=self._join_output("\n".join(raw_events), stderr),
        )

    def clear_conversation(self, conversation_id: str) -> None:
        self.session_store.delete(conversation_id)

    def _build_system_prompt(self, workspace: Path) -> str:
        conversation_id = workspace.name
        cache_key = (conversation_id, self.config.recent_summary_days)
        signature = self._prompt_signature(workspace)
        with self._prompt_snapshot_lock:
            cached = self._prompt_snapshot_cache.get(cache_key)
            if cached is not None and cached.signature == signature:
                return cached.text

        memory_snapshot = self.memory_store.read_snapshot(
            conversation_id=conversation_id,
            recent_summary_days=self.config.recent_summary_days,
        )
        parts: list[str] = []
        for filename in ["SOUL.md", "IDENTITY.md", "USER.md", "AGENT.md"]:
            path = workspace / filename
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"## {filename}\n{content}")

        if memory_snapshot.memory_text:
            parts.append(f"## MEMORY.md\n{memory_snapshot.memory_text}")

        tool_guide = self._read_tool_guide(workspace)
        if tool_guide:
            parts.append(f"## TOOL_BRIDGE.md\n{tool_guide}")

        if memory_snapshot.recent_summaries_text:
            parts.append(f"## Recent Summaries\n{memory_snapshot.recent_summaries_text}")

        snapshot = PromptSnapshot(text="\n\n".join(parts), signature=signature)
        with self._prompt_snapshot_lock:
            self._prompt_snapshot_cache[cache_key] = snapshot
        return snapshot.text

    def _build_command(
        self,
        request: AgentRequest,
        session: dict | None,
        *,
        output_format: str = "json",
    ) -> list[str]:
        if self.config.ai_provider == "claude":
            command = [
                self.config.selected_cli_path,
                "-p",
                request.text,
                "--output-format",
                output_format,
            ]
            if self.config.claude_permission_mode != "default":
                command.extend(["--permission-mode", self.config.claude_permission_mode])
            if output_format == "stream-json":
                command.extend(["--verbose", "--include-partial-messages"])
            if session and session.get("provider") == "claude" and session.get("session_id"):
                command.extend(["--resume", session["session_id"]])
            return command

        command = [
            self.config.selected_cli_path,
            "-p",
            request.text,
            "--output-format",
            output_format,
        ]
        if self.config.gemini_approval_mode != "default":
            command.extend(["--approval-mode", self.config.gemini_approval_mode])
        if session and session.get("provider") == "gemini" and session.get("resume"):
            command.extend(["--resume", session["resume"]])
        return command

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

    def _prompt_signature(self, workspace: Path) -> tuple[tuple[str, bool, int, int], ...]:
        files = [
            "SOUL.md",
            "IDENTITY.md",
            "USER.md",
            "AGENT.md",
            "MEMORY.md",
            "tools/README.md",
        ]
        signature: list[tuple[str, bool, int, int]] = []
        for relative_name in files:
            path = workspace / relative_name
            if path.exists():
                stat = path.stat()
                signature.append((relative_name, True, int(stat.st_mtime_ns), int(stat.st_size)))
            else:
                signature.append((relative_name, False, 0, 0))

        summary_snapshot = self.memory_store.read_snapshot(
            conversation_id=workspace.name,
            recent_summary_days=self.config.recent_summary_days,
        )
        for relative_name, modified_ns in summary_snapshot.signature:
            summary_path = workspace / relative_name
            size = int(summary_path.stat().st_size) if summary_path.exists() else 0
            signature.append((relative_name, summary_path.exists(), modified_ns, size))
        return tuple(signature)

    def _read_tool_guide(self, workspace: Path) -> str:
        guide_path = workspace / "tools" / "README.md"
        if not guide_path.exists():
            return ""
        return guide_path.read_text(encoding="utf-8").strip()

    def _write_context_file(self, workspace: Path, system_prompt: str) -> None:
        context_file = workspace / self.config.context_filename
        alternate_file = workspace / ("CLAUDE.md" if self.config.context_filename == "GEMINI.md" else "GEMINI.md")
        if alternate_file.exists():
            alternate_file.unlink()
        if system_prompt:
            context_file.write_text(system_prompt.rstrip() + "\n", encoding="utf-8")
        elif context_file.exists():
            context_file.unlink()

    def _load_session(self, conversation_id: str) -> dict | None:
        session = self.session_store.get(conversation_id)
        if not session:
            return None
        provider = session.get("provider")
        if provider and provider != self.config.ai_provider:
            return None
        return session

    def _save_session(self, conversation_id: str, session_id: str) -> None:
        payload = {
            "provider": self.config.ai_provider,
            "session_id": session_id,
        }
        if self.config.ai_provider == "gemini":
            payload["resume"] = "latest"
        self.session_store.set(conversation_id, payload)

    def _stream_output_format(self) -> str:
        return "stream-json"

    def _provider_label(self) -> str:
        return "Claude" if self.config.ai_provider == "claude" else "Gemini"

    def _parse_output(self, stdout: str, stderr: str) -> AgentResult:
        raw_output = self._join_output(stdout, stderr)
        if not stdout:
            return AgentResult(text=stderr or f"{self._provider_label()} CLI returned no output.", raw_output=raw_output)

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return AgentResult(text=stdout, raw_output=raw_output)

        if self.config.ai_provider == "claude":
            return self._parse_claude_output(data, raw_output, stderr)
        return self._parse_gemini_output(data, raw_output, stderr)

    def _parse_gemini_output(self, data: dict, raw_output: str, stderr: str) -> AgentResult:
        error = data.get("error")
        if isinstance(error, dict):
            error_message = error.get("message") or json.dumps(error, ensure_ascii=False)
            return AgentResult(
                text=error_message,
                raw_output=raw_output,
                session_id=data.get("session_id"),
                model=data.get("model"),
            )

        response = data.get("response")
        return AgentResult(
            text=response or stderr or json.dumps(data, ensure_ascii=False),
            raw_output=raw_output,
            session_id=data.get("session_id"),
            model=data.get("model"),
        )

    def _parse_claude_output(self, data: dict, raw_output: str, stderr: str) -> AgentResult:
        result = data.get("result")
        if isinstance(result, str) and result:
            text = result
        else:
            text = stderr or json.dumps(data, ensure_ascii=False)
        return AgentResult(
            text=text,
            raw_output=raw_output,
            session_id=data.get("session_id"),
            model=data.get("model") or data.get("fallback_model"),
        )

    def _parse_stream_event(self, raw_line: str) -> AgentStreamEvent:
        try:
            data = json.loads(raw_line)
        except json.JSONDecodeError:
            return AgentStreamEvent(delta=raw_line, raw_event=raw_line)

        if self.config.ai_provider == "claude":
            return self._parse_claude_stream_event(data, raw_line)
        return self._parse_gemini_stream_event(data, raw_line)

    def _parse_gemini_stream_event(self, data: dict, raw_line: str) -> AgentStreamEvent:
        event_type = data.get("type")
        if event_type == "init":
            return AgentStreamEvent(session_id=data.get("session_id"), model=data.get("model"), raw_event=raw_line)
        if event_type == "message" and data.get("role") == "assistant":
            content = data.get("content")
            if isinstance(content, str):
                return AgentStreamEvent(delta=content, raw_event=raw_line)
        if event_type == "result":
            status = data.get("status")
            if status != "success":
                return AgentStreamEvent(done=True, raw_event=raw_line, error=status or "result_error")
            return AgentStreamEvent(done=True, raw_event=raw_line)
        if event_type == "error":
            error = data.get("error")
            if isinstance(error, dict):
                message = error.get("message") or json.dumps(error, ensure_ascii=False)
            else:
                message = json.dumps(data, ensure_ascii=False)
            return AgentStreamEvent(text=message, done=True, raw_event=raw_line, error="stream_error")
        return AgentStreamEvent(raw_event=raw_line)

    def _parse_claude_stream_event(self, data: dict, raw_line: str) -> AgentStreamEvent:
        event_type = data.get("type")
        if event_type == "system":
            return AgentStreamEvent(
                session_id=data.get("session_id"),
                model=data.get("model"),
                raw_event=raw_line,
            )
        if event_type == "assistant":
            message = data.get("message")
            if isinstance(message, dict):
                model = message.get("model")
                content = message.get("content")
                if isinstance(content, list):
                    text_parts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
                    text = "".join(text_parts)
                    if text:
                        return AgentStreamEvent(delta=text, raw_event=raw_line, model=model)
        if event_type == "result":
            result_text = data.get("result")
            if data.get("is_error"):
                return AgentStreamEvent(text=result_text or json.dumps(data, ensure_ascii=False), done=True, raw_event=raw_line, error="result_error")
            return AgentStreamEvent(done=True, raw_event=raw_line, session_id=data.get("session_id"))
        return AgentStreamEvent(raw_event=raw_line)

    def _join_output(self, stdout: str, stderr: str) -> str:
        outputs = [part for part in [stdout, stderr] if part]
        return "\n".join(outputs)
