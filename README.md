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

Startup self-checks now validate these on boot, and also verify that the configured Gemini CLI is available on `PATH` and that `WORKSPACE_ROOT` / `DATA_ROOT` resolve to usable directories.

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
3. Run startup self-checks for required config, CLI availability, and core directories
4. Create Dispatcher, Feishu gateway, and scheduler loop
5. Start scheduler polling thread
6. Start Feishu WebSocket client when credentials are present

See `app/main.py:10`.

## Operator Runbook

### 1. Start modes

#### Real Feishu mode
Use real Feishu credentials in `.env`. On startup, the service will first validate required Feishu config and Gemini CLI availability, then:
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
- Scheduler loop starts in a background thread: `app/scheduler/loop.py:28`
- It polls every `POLL_INTERVAL_SECONDS`: `app/scheduler/loop.py:36`
- Due tasks are claimed before dispatch, so an already-running task is skipped instead of being re-entered: `app/scheduler/loop.py:41`, `app/scheduler/store.py:70`
- Stale task locks are reclaimed after a fixed timeout (currently 600 seconds): `app/scheduler/loop.py:24`, `app/scheduler/store.py:171`
- Due tasks are delivered back through dispatcher and then to Feishu: `app/scheduler/loop.py:59`
- One-time tasks are removed after successful execution; cron tasks get `next_run_at` recalculated; failed tasks release the lock and remain eligible for retry: `app/scheduler/store.py:99`, `app/scheduler/store.py:134`

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

### 8. Startup self-checks
On startup, the app now fails fast for:
- missing `FEISHU_APP_ID` / `FEISHU_APP_SECRET`
- missing or empty `GEMINI_CLI_PATH`
- configured Gemini CLI not found on `PATH`
- unusable `WORKSPACE_ROOT` / `DATA_ROOT`

It also emits a warning when `GEMINI_API_KEY` is not set, because Gemini CLI may still work through an existing local login session.

Implementation lives in `app/config.py:53` and is invoked from `app/main.py:12`.

### 9. Troubleshooting

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
- skills extension framework is not implemented yet
