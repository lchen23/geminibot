# GeminiBot

A local-first Feishu AI assistant implemented in Python and using Gemini CLI as its reasoning runtime.

## Status
Current repo status:
- Feishu gateway receive/send path is implemented
- Dispatcher, memory, scheduler, and workspace storage are implemented
- Gemini CLI adapter is wired into the main chat loop
- Local tool bridge for memory and scheduler is implemented
- Natural-language tool invocation acceptance has passed with Gemini CLI
- Skills framework and some hardening items are still pending

## Requirements
- Python 3.11+
- Gemini CLI installed and available as `gemini`
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
- `GEMINI_CLI_PATH` (default and recommended: `gemini`)

Important optional fields:
- `DEFAULT_TIMEZONE` — default schedule timezone
- `WORKSPACE_ROOT` — per-conversation workspace directory
- `DATA_ROOT` — shared JSON state directory
- `POLL_INTERVAL_SECONDS` — scheduler polling interval
- `LOG_LEVEL` — application log level

Current config loading and defaulting live in `app/config.py:30`.

## Run
Start the service:

```bash
python -m app.main
```

Startup flow:
1. Load environment and initialize data/workspace directories
2. Configure logging
3. Create Dispatcher, Feishu gateway, and scheduler loop
4. Start scheduler polling thread
5. Start Feishu WebSocket client when credentials are present

See `app/main.py:10`.

## Operator Runbook

### 1. Start modes

#### Real Feishu mode
Use real Feishu credentials in `.env`. On startup, the service will:
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
- All inbound messages go through `Dispatcher.handle()`: `app/dispatcher.py:32`
- Built-in commands are handled before Gemini: `app/dispatcher.py:35`
- Other messages are sent to Gemini CLI through `GeminiAgentEngine.run()`: `app/dispatcher.py:52`, `app/agent/engine.py:39`
- Daily logs are appended after each handled message: `app/dispatcher.py:63`, `app/memory/store.py:13`
- Scheduled tasks are routed back into the same dispatcher path: `app/dispatcher.py:73`, `app/scheduler/loop.py:40`

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
- `GEMINI.md` — assembled runtime context
- `logs/*.md` — daily chat logs
- `summaries/*.md` — memory summaries
- `tools/tool_bridge.py` — local tool bridge entrypoint
- `tool_audit.jsonl` — tool invocation audit log

Workspace bootstrap lives in `app/agent/workspace.py:219`.

### 5. Tool bridge operations
Gemini can use local tools from the workspace shell:

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

### 6. Scheduler operations
- Scheduler loop starts in a background thread: `app/scheduler/loop.py:27`
- It polls every `POLL_INTERVAL_SECONDS`: `app/scheduler/loop.py:35`
- Due tasks are delivered back through dispatcher and then to Feishu: `app/scheduler/loop.py:46`
- One-time tasks are removed after execution; cron tasks get `next_run_at` recalculated: `app/scheduler/store.py:67`

### 7. Basic acceptance checklist
Use this after config changes or deploy/restart:

1. Start the service successfully with `python -m app.main`
2. Confirm no immediate CLI-not-found error from Gemini adapter
3. Send `/help` and verify a formatted reply is returned
4. Send `/remember test memory` and verify workspace `MEMORY.md` changes
5. Send `/schedule once | 2026-04-02T15:00:00 | test reminder` and verify `data/schedules.json` changes
6. Trigger one natural-language memory request and confirm `tool_audit.jsonl` updates
7. Trigger one natural-language reminder request and confirm `data/schedules.json` updates
8. If running in Feishu mode, verify reply cards appear in the chat instead of only local fallback storage

### 8. Troubleshooting

#### Gemini CLI not found
Symptom:
- reply says Gemini CLI was not found

Check:
- `GEMINI_CLI_PATH` in `.env`
- `which gemini`
- adapter path handling in `app/agent/engine.py:47`

#### Feishu replies not delivered
Symptom:
- reply is generated but not visible in Feishu

Check:
- credentials in `.env`
- auth/send failures in logs from `app/gateway/feishu.py:91` and `app/gateway/feishu.py:114`
- fallback messages in `data/unsent_messages.json`

#### Scheduler did not fire
Check:
- task exists in `data/schedules.json`
- `next_run_at` is due
- service process is still running
- execution records in `data/schedule_runs.json`

#### Duplicate inbound events
Check:
- recent message IDs in `data/dedup.json`
- dedup handling in `app/gateway/feishu.py:54`

## Architecture References
- implementation plan: `specs/implementation-plan.md`
- task tracking and current status: `specs/task_list.md`

## Known Gaps
- no startup self-checks yet
- no overlap lock/skip logic for scheduled runs yet
- operator runbook is kept in this README for now
- skills extension framework is not implemented yet
