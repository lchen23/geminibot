# GeminiBot Implementation Plan

## Summary
This plan turns the existing GeminiBot spec into an executable build sequence. It prioritizes a usable Feishu-to-Gemini chat loop first, then adds durable session continuity and proactive scheduling. The plan is optimized for a local-first Python service with file-based persistence and minimal infrastructure.

## Delivery Strategy
- Build thin vertical slices instead of isolated modules.
- Keep every milestone runnable end-to-end.
- Prefer simple file-backed implementations before introducing abstraction.
- Validate Gemini CLI behavior early because it is the main external dependency risk.

## Assumptions
- Gemini CLI Agent is installed locally and can be invoked from shell.
- Feishu app credentials are available.
- Python 3.11+ is available.
- Local disk persistence is acceptable for v1.

## Major Risks
1. Gemini CLI session resume and JSON output behavior may differ from the conceptual model.
2. Feishu Python SDK WebSocket ergonomics may require adapter code.
3. Tool bridging between Python and Gemini CLI may need iteration.
4. Long-running scheduled tasks may overlap without a lock strategy.

## Phase 0 — Technical Validation
### Goal
De-risk external integrations before building the full app.

### Tasks
- Verify Gemini CLI invocation model:
  - single prompt execution
  - structured output mode
  - resume/session mode
  - cwd/workspace behavior
- Verify Feishu WebSocket event subscription in Python.
- Verify Feishu reply message/card API from Python.
- Decide the exact tool bridge pattern for Gemini:
  - direct MCP support if available
  - command wrapper tools otherwise

### Deliverables
- `notes/gemini-cli-validation.md`
- `notes/feishu-validation.md`
- final adapter decisions recorded in spec addendum

### Exit Criteria
- Can send one Feishu message and manually route it to Gemini CLI, then send a response back.

## Phase 1 — Bootstrap Project Skeleton
### Goal
Create a runnable Python service layout with configuration, logging, and startup flow.

### Tasks
- Create package/module structure under `app/`.
- Add `pyproject.toml`.
- Add `.env.example`.
- Add base config loader.
- Add structured logging utility.
- Add startup file `app/main.py`.
- Create initial data files if missing.

### Deliverables
- runnable `python -m app.main`
- empty but valid directory layout

### Exit Criteria
- Application starts, loads config, and initializes directories without crashing.

## Phase 2 — Feishu Gateway Vertical Slice
### Goal
Receive real text messages from Feishu and send back a static response.

### Tasks
- Implement `app/gateway/feishu.py`:
  - client initialization
  - WebSocket startup
  - event handler registration
  - text extraction
  - card sending helper
- Implement message dedup store in `data/dedup.json`.
- Define normalized `IncomingMessage` model.
- Add a temporary echo/stub dispatcher.

### Deliverables
- live Feishu echo bot with Markdown card replies

### Exit Criteria
- User sends `hello` in Feishu and receives a formatted reply from the Python service.

## Phase 3 — Dispatcher and Core Request Lifecycle
### Goal
Introduce the central orchestration layer.

### Tasks
- Implement `app/dispatcher.py`.
- Normalize gateway/scheduler inputs to one internal request shape.
- Add built-in command parsing for:
  - `/help`
  - `/clear`
  - `/tasks`
- Add card rendering adapter.

### Deliverables
- stable request pipeline with internal command handling

### Exit Criteria
- Gateway no longer replies directly; all responses pass through Dispatcher.

## Phase 4 — Gemini CLI Adapter and Chat Loop
### Goal
Replace stub replies with Gemini CLI execution.

### Tasks
- Implement `app/agent/engine.py`.
- Implement `app/agent/workspace.py`.
- Implement `app/agent/session_store.py`.
- Create per-conversation workspace bootstrap from templates.
- Build base system prompt from persona files.
- Invoke Gemini CLI via subprocess.
- Parse output and persist session metadata.
- Return structured result object to Dispatcher.

### Deliverables
- end-to-end Feishu -> Dispatcher -> Gemini -> Feishu flow

### Exit Criteria
- Multi-turn conversation works for a single Feishu chat.

## Phase 5 — Persona and Workspace System
### Goal
Make the assistant feel persistent while keeping state minimal.

### Tasks
- Create template files:
  - `SOUL.md`
  - `IDENTITY.md`
  - `USER.md`
  - `AGENT.md`
- Implement workspace initialization from templates.
- Ensure persona files are injected on every agent run.
- Add session metadata file per workspace.

### Deliverables
- per-conversation workspace model with persona files and session metadata

### Exit Criteria
- Editing workspace persona files changes assistant behavior on the next turn.
- Restarting the service preserves Gemini session continuity for an existing conversation.

## Phase 6 — Scheduler v1
### Goal
Allow proactive tasks via cron and once schedules.

### Tasks
- Implement `app/scheduler/store.py`.
- Implement `app/scheduler/loop.py`.
- Add polling loop startup in `main.py`.
- Support cron and one-time schedules.
- Route due tasks through Dispatcher.
- Add `/tasks` command.
- Add schedule execution logging.

### Deliverables
- proactive reminders and recurring tasks

### Exit Criteria
- User can create a reminder and receive it at the scheduled time in Feishu.

## Phase 7 — Tool Bridge for Gemini
### Goal
Expose Python-side capabilities to the Gemini agent cleanly.

### Tasks
- Finalize one tool bridge approach:
  - MCP if Gemini CLI supports it well
  - otherwise subprocess-exposed local command tools
- Expose scheduler tools.
- Define input/output schemas.
- Add audit logging for tool invocations.

### Deliverables
- Gemini agent can create and manage schedules without hardcoded dispatcher shortcuts

### Exit Criteria
- Agent can create a schedule from natural language.

## Phase 8 — Hardening and Operator Experience
### Goal
Make the system maintainable for daily use.

### Tasks
- Improve error messages and fallback cards.
- Add atomic file writes for JSON stores.
- Add lock or skip logic for overlapping scheduled task runs.
- Add startup self-checks for required config.
- Add README and operator runbook.

### Deliverables
- daily-usable local assistant service

### Exit Criteria
- Service can restart safely and recover prior state from disk.

## Suggested Milestone Breakdown

### Milestone A
- Phase 0
- Phase 1
- Phase 2

### Milestone B
- Phase 3
- Phase 4
- Phase 5

### Milestone C
- Phase 6
- Phase 7

### Milestone D
- Phase 8

## Test Plan by Phase

### Core Tests
- config loading
- workspace bootstrap
- dedup file updates
- session persistence
- schedule next-run calculation

### Integration Tests
- Feishu message -> Dispatcher -> stub reply
- Feishu message -> Gemini CLI -> reply
- `/clear` -> session reset -> new conversation
- schedule creation -> due execution -> Feishu delivery

### Manual Acceptance Tests
- restart process and continue conversation
- clear a conversation session and verify a fresh Gemini run
- run one-time reminder
- run daily recurring reminder

## Initial File Creation Priorities
Create these first for the skeleton:
- `pyproject.toml`
- `.env.example`
- `app/main.py`
- `app/config.py`
- `app/dispatcher.py`
- `app/gateway/feishu.py`
- `app/agent/engine.py`
- `app/agent/workspace.py`
- `app/agent/session_store.py`
- `app/scheduler/store.py`
- `app/scheduler/loop.py`
- `app/rendering/cards.py`
- templates markdown files

## Recommended First Coding Order
1. config + startup
2. Feishu gateway echo
3. dispatcher
4. Gemini adapter
5. workspace bootstrap
6. scheduler
7. tool bridge
8. hardening

## Definition of Done
The implementation is considered successful when:
- the service runs locally from a single command
- Feishu chat works end-to-end through Gemini CLI
- Gemini session continuity persists on disk across restarts
- scheduled reminders work
- code structure matches the spec closely enough for future iteration
