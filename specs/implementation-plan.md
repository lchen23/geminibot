# GeminiBot Implementation Plan

## Summary
This plan turns `specs/spec.md` into an executable implementation sequence for a local-first Feishu AI assistant. The system is centered on five core runtime areas: Feishu gateway, dispatcher, provider-selectable agent engine, memory, and scheduler. The plan assumes a lightweight Python service, file-based persistence, and operation on a personal machine or private server.

## Planning Goals
- Keep the implementation aligned with the current spec and repository shape.
- Prioritize end-to-end usability over isolated subsystem completeness.
- Preserve local observability through readable files under `~/geminibot`.
- Support both Gemini CLI and Claude Code CLI through one adapter layer.
- Focus follow-up work on hardening and closing known gaps rather than adding new subsystems.

## Delivery Strategy
- Build thin vertical slices first, then harden them.
- Prefer simple file-backed implementations before introducing more abstraction.
- Keep one canonical request path for user messages and scheduled tasks.
- Treat provider differences as adapter concerns, not product-level forks.
- Make every milestone testable from Feishu or from a local simulation path.

## Assumptions
- Python 3.11+ is available.
- A Feishu app with valid credentials is available.
- At least one supported CLI runtime is available on `PATH`.
- Local disk persistence is acceptable for v1.
- Text messages are the primary interaction mode in v1.

## Major Risks
1. Gemini CLI and Claude Code CLI may differ in resume semantics, streaming output, and permission controls.
2. Feishu WebSocket and card APIs may require fallback logic for local reliability.
3. Memory consolidation quality may lag behind raw logging correctness.
4. Long-running scheduled tasks may expose edge cases in overlapping execution and recovery.
5. JSON-backed state can become fragile without explicit atomic write and corruption-handling rules.

## Current Baseline
The current design assumes these repository-level decisions:
- `AI_PROVIDER` selects `gemini` or `claude`.
- Session metadata is stored in `data/sessions.json`.
- Workspaces contain persona files, logs, summaries, and local tool bridge assets.
- Gateway performs deduplication before dispatch.
- Dispatcher handles built-in commands including `/schedule <once|cron> | <time-or-cron> | <prompt>` and `/delete-task <task_id>`.
- Memory and scheduler capabilities are exposed through a workspace-local tool bridge.

---

## Phase 0 — Technical Validation
### Goal
De-risk the external integrations and confirm the provider model before additional hardening work.

### Tasks
- Verify Gemini CLI behavior for:
  - one-shot execution
  - JSON output
  - stream-json output
  - resume behavior
  - workspace cwd behavior
  - approval mode wiring
- Verify Claude Code CLI behavior for:
  - one-shot execution
  - JSON output
  - stream-json output
  - resume behavior
  - permission mode wiring
- Verify Feishu WebSocket event subscription in Python.
- Verify Feishu card delivery and streaming card update APIs.
- Verify the local subprocess-based tool bridge contract.

### Deliverables
- provider behavior notes
- validated Feishu integration notes
- confirmed adapter assumptions for both CLI providers

### Exit Criteria
- A real or simulated Feishu message can be routed through the selected provider and produce a reply.
- A tool bridge command can be invoked from a workspace and return structured JSON.

---

## Phase 1 — Foundation and Configuration
### Goal
Ensure the service boots reliably with the current file layout and configuration model.

### Tasks
- Maintain package structure under `app/`.
- Keep `pyproject.toml` and CLI entrypoint current.
- Maintain `.env.example` in sync with the actual config surface.
- Keep `AppConfig` aligned with spec fields:
  - `FEISHU_APP_ID`
  - `FEISHU_APP_SECRET`
  - `AI_PROVIDER`
  - `GEMINI_CLI_PATH`
  - `CLAUDE_CLI_PATH`
  - `GEMINI_APPROVAL_MODE`
  - `CLAUDE_PERMISSION_MODE`
  - `BOT_NAME`
  - `APP_ROOT`
  - `DEFAULT_TIMEZONE`
- Create required data files when missing.
- Keep startup checks focused on actionable operator errors.

### Deliverables
- runnable `python -m app.main`
- synchronized `.env.example`, README, and config loader
- initialized `data/` and `workspaces/` roots

### Exit Criteria
- Service starts cleanly with valid config.
- Missing or invalid config yields clear startup failures.

---

## Phase 2 — Feishu Gateway and Delivery Path
### Goal
Provide a stable Feishu ingress and egress layer for the rest of the system.

### Tasks
- Maintain `app/gateway/feishu.py` as the single Feishu integration point.
- Keep WebSocket connection startup and shutdown logic reliable.
- Parse inbound Feishu events into normalized `IncomingMessage` values.
- Deduplicate incoming messages in the gateway using `data/dedup.json`.
- Support normal reply delivery.
- Support streaming card replies when the environment allows it.
- Fall back gracefully from streaming to non-streaming delivery.
- Persist failed outbound payloads to `data/unsent_messages.json`.

### Deliverables
- gateway that can receive text messages and deliver replies
- deduplication and failed-delivery persistence
- optional streaming card path with fallback

### Exit Criteria
- Duplicate messages do not get reprocessed.
- A reply can be delivered or persisted for inspection if delivery fails.

---

## Phase 3 — Dispatcher and Request Lifecycle
### Goal
Keep one orchestration path for both reactive and proactive execution.

### Tasks
- Maintain `app/dispatcher.py` as the application orchestrator.
- Normalize gateway and scheduler inputs into one request shape.
- Keep built-in command handling current for:
  - `/help`
  - `/clear`
  - `/remember <text>`
  - `/tasks`
  - `/schedule <once|cron> | <time-or-cron> | <prompt>`
  - `/delete-task <task_id>`
- Support both one-shot reply flow and streaming reply flow.
- Append daily logs after each completed interaction.
- Keep scheduler-triggered tasks on the same dispatcher path.

### Deliverables
- stable internal request lifecycle
- command handling aligned with the current spec
- shared orchestration path for gateway and scheduler

### Exit Criteria
- All user-visible replies pass through Dispatcher.
- Scheduled tasks and user messages share the same request lifecycle.

---

## Phase 4 — Provider-Selectable Agent Engine
### Goal
Provide a single adapter layer over Gemini CLI and Claude Code CLI.

### Tasks
- Maintain `app/agent/engine.py`, `workspace.py`, and `session_store.py`.
- Support provider-specific command construction:
  - Gemini: `--output-format`, `--resume latest`, `--approval-mode`
  - Claude: `--output-format`, `--resume <session_id>`, `--permission-mode`
- Support both non-streaming and stream-json execution.
- Parse provider output into one internal result model.
- Persist provider-aware session metadata in `data/sessions.json`.
- Keep provider-specific context file behavior (`GEMINI.md` / `CLAUDE.md`) internal to the adapter.
- Ensure workspace cwd behavior stays deterministic.

### Deliverables
- unified internal agent engine API
- provider-aware session persistence
- streaming and non-streaming support for both CLIs

### Exit Criteria
- The same dispatcher call path works with either provider.
- Sessions survive process restarts via file-backed storage.

---

## Phase 5 — Workspace and Prompt Assembly
### Goal
Make each conversation persistent, isolated, and inspectable from disk.

### Tasks
- Maintain per-conversation workspace creation under `workspaces/<conversation_id>/`.
- Ensure template bootstrap for:
  - `SOUL.md`
  - `IDENTITY.md`
  - `USER.md`
  - `AGENT.md`
  - `MEMORY.md`
- Maintain `logs/`, `summaries/`, and `tools/` subdirectories.
- Assemble prompts from:
  1. persona files
  2. long-term memory
  3. recent summaries
  4. tool bridge guide
  5. optional proactive task context
- Keep workspace contents human-readable and editable.

### Deliverables
- consistent per-conversation workspace layout
- stable prompt assembly behavior

### Exit Criteria
- Editing workspace context files changes the next agent turn.
- Conversations remain isolated from each other.

---

## Phase 6 — Memory System v1
### Goal
Provide durable memory without a database.

### Tasks
- Maintain `app/memory/store.py` for:
  - daily log append
  - long-term memory read/write
  - summary read/write
  - recent summary loading
  - search by keyword/date
- Keep long-term memory human-readable in `MEMORY.md`.
- Maintain memory sections suitable for stable preferences and facts.
- Keep `/remember` as the explicit user-facing memory write path.
- Ensure memory is injected on every turn.

### Deliverables
- readable workspace logs, summaries, and `MEMORY.md`
- durable explicit memory across turns and restarts

### Exit Criteria
- A saved preference remains available on later turns.
- Operators can inspect memory state directly from workspace files.

---

## Phase 7 — Memory Consolidation and Background Work
### Goal
Turn raw logs into reusable summaries and keep memory maintenance off the hot path.

### Tasks
- Maintain background execution through `app/memory/worker.py`.
- Keep summary generation in `app/memory/consolidate.py`.
- Keep memory merge logic capable of rewriting `MEMORY.md` with deduplicated content.
- Trigger consolidation from `/clear`.
- Clarify and complete the `/clear` workflow so that summary generation and memory merge are explicitly coordinated.
- Improve failure handling so failed consolidation preserves raw logs and produces diagnosable errors.

### Deliverables
- asynchronous memory maintenance
- summary generation pipeline
- deterministic consolidation behavior for `/clear`

### Exit Criteria
- `/clear` resets turn-level context without losing important long-term memory.
- Consolidation failures do not corrupt raw logs or workspace state.

---

## Phase 8 — Local Tool Bridge
### Goal
Expose Python-side memory and scheduler operations to the agent through a simple local contract.

### Tasks
- Maintain the workspace-local `tools/tool_bridge.py` pattern.
- Keep `tools/README.md` generated with command usage guidance.
- Expose memory operations:
  - `memory_search`
  - `memory_list_by_date`
  - `memory_save`
- Expose scheduler operations:
  - `schedule_task`
  - `list_tasks`
  - `delete_task`
- Keep command I/O structured as JSON.
- Record tool invocations in `tool_audit.jsonl`.

### Deliverables
- local command-based bridge usable from agent workspaces
- audit trail for tool calls

### Exit Criteria
- The agent can invoke memory and scheduler tools from the workspace environment.
- Tool invocations are auditable from disk.

---

## Phase 9 — Scheduler and Proactive Execution
### Goal
Support one-time and recurring tasks through the same runtime pipeline.

### Tasks
- Maintain `app/scheduler/store.py` and `app/scheduler/loop.py`.
- Persist tasks in `data/schedules.json`.
- Persist execution history in `data/schedule_runs.json`.
- Poll for due tasks on a configurable interval.
- Support both `once` and `cron` schedules.
- Route due tasks back through Dispatcher.
- Keep overlap protection for task execution.
- Ensure scheduler failures do not crash the main service.

### Deliverables
- proactive reminder execution
- task listing and deletion support
- execution log for scheduled runs

### Exit Criteria
- A scheduled task can be created, executed, and logged.
- Overlapping runs are skipped or controlled instead of duplicated.

---

## Phase 10 — Hardening and Operator Experience
### Goal
Close the gap between functional correctness and daily reliability.

### Tasks
- Add or maintain atomic writes for JSON-backed state where practical.
- Improve corruption handling and diagnostics for JSON state files.
- Tighten provider-specific tests for resume, streaming, and error cases.
- Expand manual and automated validation for Feishu delivery failures.
- Keep README, spec, implementation plan, and task tracking aligned.
- Ensure operator-facing commands cover start, stop, restart, and status inspection.

### Deliverables
- more restart-safe local service
- clearer operator diagnostics
- documentation aligned with actual runtime behavior

### Exit Criteria
- The service can recover cleanly from normal restarts.
- Operational failures are observable from local files and logs.

---

## Recommended Milestone Breakdown

### Milestone A — Runtime Baseline
- Phase 0
- Phase 1
- Phase 2
- Phase 3

### Milestone B — Provider and Workspace Reliability
- Phase 4
- Phase 5

### Milestone C — Durable Context
- Phase 6
- Phase 7
- Phase 8

### Milestone D — Proactive Execution
- Phase 9

### Milestone E — Hardening
- Phase 10

---

## Validation Plan

### Unit-Level Coverage
- config parsing and validation
- workspace bootstrap
- session persistence
- command parsing
- memory read/write behavior
- schedule next-run calculation
- tool bridge command parsing

### Integration Coverage
- Feishu message -> Dispatcher -> provider reply
- Feishu message -> streaming reply path
- `/remember` -> memory persistence -> later recall
- `/clear` -> consolidation workflow -> later recall
- schedule creation -> due execution -> delivery -> execution log
- tool bridge command -> JSON output -> audit log

### Manual Acceptance Checks
- switch between Gemini and Claude providers
- continue a multi-turn conversation after restart
- create a one-time reminder and receive it
- create a recurring task and confirm next-run updates
- inspect logs, summaries, schedules, and sessions directly on disk
- verify fallback behavior when Feishu delivery fails

---

## Definition of Done
A phase is done when:
- the behavior described in the phase exists in code
- the primary success path is exercised by tests or manual validation
- the relevant file-backed state is inspectable on disk
- operator-facing failure behavior is understandable
- the plan, spec, and task tracking do not contradict the implementation
