# GeminiBot

A local-first Feishu AI assistant implemented in Python and using Gemini CLI or Claude CLI as its reasoning runtime.

## Status
Current repo status:
- Feishu gateway receive/send path is implemented
- Dispatcher, memory, scheduler, and workspace storage are implemented
- Provider-aware CLI adapter is wired into the main chat loop, including streaming output mode
- Local tool bridge for memory and scheduler is implemented
- Natural-language tool invocation acceptance has passed with Gemini CLI
- Real Feishu streaming card replies have passed acceptance
- Minimal dual CLI compatibility for Gemini CLI and Claude CLI is implemented
- Skills framework and some hardening items are still pending

## Requirements
- Python 3.11+
- At least one reasoning CLI installed and available on `PATH`
  - Gemini CLI, typically as `gemini`
  - Claude CLI, typically as `claude`
- Feishu app credentials for real chat delivery

## Install
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Configuration
Copy `.env.example` to `.env` and fill in the values.

Required runtime fields:
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `AI_PROVIDER` — `gemini` or `claude`
- Provider-specific CLI path for the selected runtime
  - `GEMINI_CLI_PATH` when `AI_PROVIDER=gemini`
  - `CLAUDE_CLI_PATH` when `AI_PROVIDER=claude`

Startup self-checks now validate these on boot, verify that `AI_PROVIDER` is supported, check that the selected CLI is available on `PATH`, and ensure that `WORKSPACE_ROOT` / `DATA_ROOT` resolve to usable directories.

Important optional fields:
- `DEFAULT_TIMEZONE` — default schedule timezone, using an IANA zone like `America/Los_Angeles`
- `WORKSPACE_ROOT` — per-conversation workspace directory
- `DATA_ROOT` — shared JSON state directory
- `POLL_INTERVAL_SECONDS` — scheduler polling interval
- `LOG_LEVEL` — application log level
- `GEMINI_API_KEY` — only relevant when `AI_PROVIDER=gemini`

Scheduler `once` inputs without an explicit offset are interpreted in `DEFAULT_TIMEZONE`, then normalized to UTC for storage and due-time comparison. Startup self-checks also validate that `DEFAULT_TIMEZONE` is a supported IANA timezone.

Example provider selection:

```env
AI_PROVIDER=gemini
GEMINI_CLI_PATH=gemini
CLAUDE_CLI_PATH=claude
```

Switch `AI_PROVIDER` to `claude` when you want to use Claude CLI instead of Gemini CLI.

Current config loading and defaulting live in `app/config.py:30`.

## Run
Start the service:

```bash
python -m app.main
```

Startup flow:
1. Load environment and initialize data/workspace directories
2. Configure logging
3. Run startup self-checks for required config, CLI availability, and core directories
4. Create Dispatcher, Feishu gateway, and scheduler loop
5. Start scheduler polling thread
6. Start Feishu WebSocket client when credentials are present

See `app/main.py:10`.

## Operator Runbook

### 1. Start modes

#### Real Feishu mode
Use real Feishu credentials in `.env`. On startup, the service will first validate required Feishu config and selected CLI availability, then:
- fetch tenant access token
- open Feishu WebSocket subscription
- receive inbound text messages
- send interactive card replies back to the same chat

Main entrypoints:
- gateway startup: `app/gateway/feishu.py:36`
- inbound message handling: `app/gateway/feishu.py:45`
- outbound delivery: `app/gateway/feishu.py:74`

#### Local mode
If Feishu credentials are missing, the gateway stays in local mode and skips WebSocket startup: `app/gateway/feishu.py:37`.

### 2. Core runtime behavior
- All inbound messages go through `Dispatcher.handle()` or `Dispatcher.stream_handle()`: `app/dispatcher.py:32`, `app/dispatcher.py:40`
- Built-in commands are handled before the selected AI provider: `app/dispatcher.py:91`
- Non-command messages can use the normal provider path or the streaming provider path: `app/dispatcher.py:84`, `app/agent/engine.py:51`, `app/agent/engine.py:90`
- In real Feishu mode, replies prefer CardKit streaming cards and fall back to the existing single-shot reply path if card creation/update fails: `app/gateway/feishu.py:96`, `app/gateway/feishu.py:164`
- Daily logs are appended after each handled message: `app/dispatcher.py:110`, `app/memory/store.py:13`
- Scheduled tasks are routed back into the same dispatcher path: `app/dispatcher.py:61`, `app/scheduler/loop.py:47`

### 3. Supported operator commands
In Feishu or any injected text channel, the dispatcher currently supports:
- `/help`
- `/clear`
- `/remember <text>`
- `/tasks`
- `/schedule <once|cron> | <time-or-cron> | <prompt>`
- `/delete-task <task_id>`

Command parsing lives in `app/dispatcher.py:35`.

### 4. Persistent state locations
Shared state under `DATA_ROOT`:
- `dedup.json` — recently seen message IDs
- `sessions.json` — conversation -> Gemini session metadata
- `schedules.json` — persisted schedules
- `schedule_runs.json` — scheduler execution history
- `unsent_messages.json` — fallback queue when Feishu delivery fails

Per-conversation state under `WORKSPACE_ROOT/<conversation_id>/`:
- `SOUL.md`, `IDENTITY.md`, `USER.md`, `AGENT.md`, `MEMORY.md`
- `GEMINI.md` or `CLAUDE.md` — assembled runtime context for the selected provider
- `logs/*.md` — daily chat logs
- `summaries/*.md` — memory summaries
- `tools/tool_bridge.py` — local tool bridge entrypoint
- `tool_audit.jsonl` — tool invocation audit log

Workspace bootstrap lives in `app/agent/workspace.py:219`.

### 5. Streaming reply behavior
- The runtime supports provider-aware `stream-json` handling and emits incremental assistant deltas through a shared stream path: `app/agent/engine.py:90`, `app/agent/engine.py:336`
- Gemini uses its existing `stream-json` event format; Claude uses `stream-json` plus `--verbose --include-partial-messages`: `app/agent/engine.py:204`, `app/agent/engine.py:219`
- FeishuGateway creates a CardKit card with `streaming_mode=true`, sends that card into chat, and updates a single markdown element as new deltas arrive: `app/gateway/feishu.py:180`, `app/gateway/feishu.py:205`, `app/gateway/feishu.py:255`
- Streaming element IDs must be short Feishu-safe identifiers; the current implementation uses a letter-prefixed 13-character ID: `app/gateway/feishu.py:202`
- If CardKit streaming fails at any point, the gateway logs the error and falls back to the existing non-streaming reply path: `app/gateway/feishu.py:100`
- Gemini real-stream acceptance has passed; Claude provider-mode parsing and command construction have been validated, but a fully successful online Claude E2E reply still depends on external CLI connectivity/auth availability

### 6. Tool bridge operations
The selected provider can use local tools from the workspace shell:

```bash
python tools/tool_bridge.py memory_save --content "User prefers concise replies."
python tools/tool_bridge.py schedule_task --schedule-type once --schedule-value "2026-04-02T15:00:00" --prompt "提醒我开会"
```

Current bridge commands:
- `memory_search`
- `memory_list_by_date`
- `memory_save`
- `schedule_task`
- `list_tasks`
- `delete_task`

Definitions live in `app/agent/workspace.py:63` and are backed by:
- `app/memory/tools.py:7`
- `app/scheduler/tools.py:7`

### 7. Scheduler operations
- Scheduler loop starts in a background thread: `app/scheduler/loop.py:28`
- It polls every `POLL_INTERVAL_SECONDS`: `app/scheduler/loop.py:36`
- Due tasks are compared in UTC with timezone-aware timestamps, avoiding naive local-time vs UTC drift: `app/scheduler/loop.py:41`, `app/scheduler/store.py:26`
- `once` schedules without an explicit offset are interpreted in the configured task/default timezone and stored as UTC timestamps: `app/scheduler/store.py:39`, `app/scheduler/store.py:177`
- `cron` schedules are evaluated in the task/default timezone and then normalized back to UTC for persistence: `app/scheduler/store.py:188`
- Due tasks are claimed before dispatch, so an already-running task is skipped instead of being re-entered: `app/scheduler/loop.py:41`, `app/scheduler/store.py:78`
- Stale task locks are reclaimed after a fixed timeout (currently 600 seconds): `app/scheduler/loop.py:24`, `app/scheduler/store.py:193`
- Due tasks are delivered back through dispatcher and then to Feishu: `app/scheduler/loop.py:59`
- One-time tasks are removed after successful execution; cron tasks get `next_run_at` recalculated; failed tasks release the lock and remain eligible for retry: `app/scheduler/store.py:107`, `app/scheduler/store.py:147`

### 8. Basic acceptance checklist
Use this after config changes or deploy/restart:

1. Start the service successfully with `python -m app.main`
2. Confirm no immediate CLI-not-found error from the selected provider adapter
3. Send `/help` and verify a formatted reply is returned
4. Send `/remember test memory` and verify workspace `MEMORY.md` changes
5. Send `/schedule once | 2026-04-02T15:00:00 | test reminder` and verify `data/schedules.json` stores a UTC `next_run_at` that matches `DEFAULT_TIMEZONE`
6. Trigger one natural-language memory request and confirm `tool_audit.jsonl` updates
7. Trigger one natural-language reminder request and confirm `data/schedules.json` updates
8. If running in Feishu mode, verify reply cards appear in the chat instead of only local fallback storage
9. Send a normal non-command message in Feishu and verify the reply appears as an incrementally updating streaming card
10. For real Feishu scheduler validation, compare the scheduled local wall-clock time with `data/schedule_runs.json` and allow up to one poll interval of trigger delay
11. If `AI_PROVIDER=claude`, run a provider smoke test and confirm the workspace writes `CLAUDE.md` instead of `GEMINI.md`

### 9. Startup self-checks
On startup, the app now fails fast for:
- missing `FEISHU_APP_ID` / `FEISHU_APP_SECRET`
- unsupported `AI_PROVIDER`
- missing or empty selected CLI path
- configured selected CLI not found on `PATH`
- unsupported `DEFAULT_TIMEZONE`
- unusable `WORKSPACE_ROOT` / `DATA_ROOT`

It also emits a warning when `GEMINI_API_KEY` is not set while `AI_PROVIDER=gemini`, because Gemini CLI may still work through an existing local login session.

Implementation lives in `app/config.py:53` and is invoked from `app/main.py:12`.

### 10. Troubleshooting

#### Selected CLI not found
Symptom:
- reply says the selected provider CLI was not found

Check:
- `AI_PROVIDER` in `.env`
- `GEMINI_CLI_PATH` or `CLAUDE_CLI_PATH` in `.env`
- `which gemini` or `which claude`
- provider-aware path handling in `app/config.py:60`, `app/agent/engine.py:204`

#### Feishu replies not delivered
Symptom:
- reply is generated but not visible in Feishu

Check:
- credentials in `.env`
- auth/send failures in logs from `app/gateway/feishu.py:91` and `app/gateway/feishu.py:114`
- fallback messages in `data/unsent_messages.json`

#### Streaming card fallback triggered
Symptom:
- reply appears as a normal one-shot card instead of a streaming card

Check:
- CardKit create/update errors in gateway logs
- whether `element_id` matches Feishu constraints (letter-prefixed, only letters/numbers/underscores, max 20 chars)
- whether the installed `lark-oapi` build exposes CardKit APIs used in `app/gateway/feishu.py:164`

#### Scheduler did not fire
Check:
- task exists in `data/schedules.json`
- `next_run_at` is due
- service process is still running
- execution records in `data/schedule_runs.json`
- whether the task is currently `running=true` and being skipped for overlap protection
- whether a stale lock should have been reclaimed based on `started_at`

#### Duplicate inbound events
Check:
- recent message IDs in `data/dedup.json`
- dedup handling in `app/gateway/feishu.py:54`

## Architecture References
- implementation plan: `specs/implementation-plan.md`
- task tracking and current status: `specs/task_list.md`

## Known Gaps
- startup self-checks exist, but warning/error policy is still minimal and not yet configurable
- scheduler overlap protection exists, but stale timeout is still hard-coded and there is no richer retry/backoff policy yet
- operator runbook is kept in this README for now
- Claude provider-mode compatibility is implemented, but full successful Claude online E2E acceptance is still pending external connectivity/auth stability
- skills extension framework is not implemented yet
