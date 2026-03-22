# GeminiBot Feishu AI Assistant Spec

## Summary
Design a Python-based Feishu AI assistant inspired by the OpenClaw-style architecture in the referenced article. The assistant runs as a long-lived local service, receives Feishu messages over WebSocket, delegates reasoning and execution to Gemini CLI Agent, maintains persistent memory, and supports proactive scheduled tasks. The system should be simple to operate on a personal machine, avoid unnecessary infrastructure, and keep all core state human-readable on disk.

## Goals
- Build a Feishu AI assistant in Python that can chat, remember, and proactively execute tasks.
- Use Gemini CLI Agent as the underlying agent runtime instead of Claude Code CLI.
- Preserve the article’s modular architecture: gateway, agent engine, memory, scheduler, and skill/tool extensions.
- Keep deployment lightweight: local process, no public callback URL, file-based state, minimal external dependencies.
- Produce an implementation-friendly design that can evolve into a personal assistant for content creation, reminders, and workflow automation.

## Non-Goals
- Reimplement a full agent framework from scratch.
- Build a distributed, multi-tenant SaaS architecture.
- Depend on a database for the first version.
- Provide hard guarantees for exactly-once task execution across crashes.
- Support every Feishu message type in v1; text messages are the primary target.

## Design Principles
- **Local-first**: run on a developer laptop or private server.
- **Readable state**: memory, persona, session metadata, and schedules are stored as plain files.
- **Agent-native**: let Gemini CLI Agent own reasoning and tool invocation as much as possible.
- **Loose coupling**: each module can be replaced independently.
- **Safe autonomy**: proactive execution is supported, but external side effects should be explicit and auditable.

## User Stories
- As a user, I can send a Feishu message and receive a contextual reply.
- As a user, I can tell the assistant to remember preferences across future conversations.
- As a user, I can ask the assistant to remind me later or run a recurring task.
- As a user, I can give the assistant a content-generation workflow and have it proactively deliver outputs in Feishu.
- As an operator, I can inspect memory, schedules, and per-conversation state from files without using a database admin tool.

## High-Level Architecture
The system consists of five primary modules:

1. **Feishu Gateway**
   - Maintains a WebSocket connection to Feishu.
   - Receives user messages and sends replies/cards.
   - Performs message deduplication and normalization.

2. **Dispatcher**
   - Converts incoming Feishu events into internal requests.
   - Routes requests to the Gemini agent engine.
   - Persists conversation logs.
   - Handles built-in commands like `/clear`, `/remember`, `/schedule`, `/tasks`.

3. **Gemini Agent Engine**
   - Invokes Gemini CLI Agent through Python subprocess management.
   - Maintains per-conversation sessions/workspaces.
   - Injects system prompt, persona, memory, and recent summaries.
   - Returns structured results for Feishu rendering.

4. **Memory System**
   - Stores raw daily logs, recent summaries, and long-term memory.
   - Exposes memory operations as tools callable by the agent.
   - Supports both automatic consolidation and agent-initiated saves.

5. **Scheduler**
   - Stores cron and one-time jobs.
   - Periodically checks due tasks.
   - Re-invokes the same Dispatcher/Agent pipeline for proactive execution.

## Proposed Repository Layout
```text
~/geminibot/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── dispatcher.py
│   ├── gateway/
│   │   └── feishu.py
│   ├── agent/
│   │   ├── engine.py
│   │   ├── workspace.py
│   │   └── session_store.py
│   ├── memory/
│   │   ├── store.py
│   │   ├── consolidate.py
│   │   └── tools.py
│   ├── scheduler/
│   │   ├── loop.py
│   │   ├── store.py
│   │   └── tools.py
│   ├── rendering/
│   │   └── cards.py
│   └── utils/
├── templates/
│   ├── SOUL.md
│   ├── IDENTITY.md
│   ├── USER.md
│   ├── AGENT.md
│   └── MEMORY.md
├── workspaces/
│   └── <conversation_id>/
├── data/
│   ├── schedules.json
│   ├── sessions.json
│   └── dedup.json
└── specs/
    └── spec.md
```

## Module Details

### 1. Feishu Gateway
#### Responsibilities
- Authenticate with Feishu app credentials.
- Open and maintain WebSocket event connection.
- Subscribe to `im.message.receive_v1`.
- Parse message payloads into a normalized internal format.
- Reply with Markdown cards or plain text.

#### Why WebSocket
Following the article’s architecture, WebSocket avoids the need for public ingress and lowers operational complexity.

#### Python Stack
- `lark-oapi` or Feishu Python SDK
- Native asyncio event loop

#### Input Model
```python
class IncomingMessage(TypedDict):
    message_id: str
    chat_id: str
    user_id: str
    conversation_id: str
    text: str
    sent_at: str
```

#### Output Model
```python
class OutgoingMessage(TypedDict):
    chat_id: str
    reply_to_message_id: str | None
    markdown: str
    footer: str | None
```

#### Requirements
- Deduplicate messages by `message_id`.
- Normalize rich text to plain text in v1.
- Support replying in the original chat context.

---

### 2. Dispatcher
#### Responsibilities
- Acts as the application orchestrator.
- Receives normalized input from the gateway or scheduler.
- Resolves built-in commands before invoking the agent.
- Writes daily logs after each interaction.

#### Command Set for v1
- `/clear`: clear session context and trigger memory consolidation
- `/remember <text>`: force-save a user preference/fact
- `/schedule <natural language>`: ask the agent to create a schedule
- `/tasks`: list current scheduled tasks
- `/help`: show supported commands

#### Flow
```text
Gateway/Scheduler -> Dispatcher
  -> built-in command?
     -> yes: execute internal command and reply
     -> no: call GeminiAgentEngine.run()
  -> append daily log
  -> render Feishu card
```

#### Requirements
- Must be the single entry point for both reactive and proactive execution.
- Must isolate conversation keys from Feishu-specific event details.

---

### 3. Gemini Agent Engine
#### Responsibilities
- Prepare per-conversation workspaces.
- Invoke Gemini CLI Agent via subprocess.
- Maintain conversation continuity across multiple user turns.
- Append persona and memory context to each invocation.

#### Why Gemini CLI Agent
The user requirement is to use Gemini CLI Agent as the reasoning/execution backend. The Python service should treat it as an external agent runtime with:
- file and shell capabilities delegated to the CLI agent
- session or checkpoint reuse if Gemini CLI supports it
- structured output parsing for reliable integration

#### Invocation Strategy
The engine should wrap Gemini CLI in a stable adapter layer.

Example conceptual call:
```bash
gemini -p "<user message>" --output-format json --session <session_id>
```

If Gemini CLI uses different flags, the adapter should map internal concepts to actual CLI arguments.

#### Workspace Strategy
Each Feishu conversation gets a dedicated workspace:
```text
~/geminibot/workspaces/<conversation_id>/
├── SOUL.md
├── IDENTITY.md
├── USER.md
├── AGENT.md
├── MEMORY.md
├── tools/
├── logs/
└── session.json
```

#### Persona Files
Borrowing the article’s pattern, these files shape assistant behavior:
- `SOUL.md`: values, tone, behavior boundaries
- `IDENTITY.md`: bot name, role, persona
- `USER.md`: known user preferences and profile
- `AGENT.md`: execution checklist and operating rules
- `MEMORY.md`: long-term facts

#### System Prompt Assembly
Each run should compose:
1. base system prompt
2. SOUL / IDENTITY / USER / AGENT files
3. long-term MEMORY.md
4. recent summaries from the last N days
5. optional schedule/task context if invoked proactively

#### Requirements
- Per-conversation isolation is required.
- The engine must capture stdout/stderr and parse structured output.
- Session continuity must survive process restarts via file-backed session storage.

---

### 4. Memory System
#### Goals
Enable the assistant to become more personalized over time without requiring a database.

#### Two-Layer Write Model
1. **Short-term logs**
   - Every interaction is appended to `logs/YYYY-MM-DD.md`.
   - Raw material for future summarization.

2. **Long-term memory**
   - Stable facts are stored in `MEMORY.md`.
   - Updated via explicit command or agent tool.

#### Three-Layer Read Model
1. **Always-on long-term memory**
   - `MEMORY.md` injected every turn.
2. **Recent summaries**
   - Last 7 days’ summaries injected every turn.
3. **On-demand search**
   - Agent can search logs by keyword/date using tools.

#### Memory Files
```text
workspaces/<conversation_id>/logs/2026-03-22.md
workspaces/<conversation_id>/MEMORY.md
workspaces/<conversation_id>/summaries/2026-03-22.md
```

#### Agent Tools
The Python service should expose memory operations to Gemini Agent via a local tool bridge.

Required tool set:
- `memory_search(query, limit=10)`
- `memory_list_by_date(start_date, end_date)`
- `memory_save(content)`

#### Consolidation
On `/clear` or scheduled maintenance, run a consolidation job:
- read new raw logs
- summarize important events
- rewrite recent summary
- rewrite `MEMORY.md` with deduplicated high-value facts

#### Requirements
- Memory must be human-readable and editable.
- Long-term memory should be rewritten, not append-only.
- Search can be keyword-based in v1; semantic retrieval is an enhancement.

---

### 5. Scheduler
#### Responsibilities
- Support cron and one-time jobs.
- Persist jobs to disk.
- Run a polling loop and dispatch due jobs through the same pipeline.

#### Job Model
```python
class Task(TypedDict):
    id: str
    chat_id: str
    conversation_id: str
    prompt: str
    schedule_type: Literal["cron", "once"]
    schedule_value: str
    timezone: str
    next_run_at: str
    created_by: str
    enabled: bool
```

#### Storage
Use `~/geminibot/data/schedules.json` in v1.

#### Execution Loop
- Poll every 30 seconds.
- Load enabled tasks.
- Trigger due tasks.
- For cron jobs, compute next occurrence.
- For one-time jobs, remove or mark completed.

#### Agent Tooling
Expose scheduler tools:
- `schedule_task(prompt, schedule_type, schedule_value, chat_id, conversation_id, timezone)`
- `list_tasks(chat_id=None)`
- `delete_task(task_id)`

#### Requirements
- Scheduled tasks must execute through Dispatcher, not via a separate shortcut path.
- Scheduler failures should not crash the gateway.
- Each job execution should be logged to disk.

---

### 6. Skill/Tool Extension Model
#### Goal
Provide a clean way to teach the assistant specialized business workflows.

#### Design
Instead of tightly coupling all features into the core service, define a lightweight plugin/skill mechanism.
Each skill may include:
- instruction markdown
- Python wrappers around APIs
- templates for content generation
- validation rules for external side effects

#### Initial Skill Targets
- WeChat article generation/publishing
- Xiaohongshu content generation/publishing
- Daily brief generation
- Personal reminders and recurring reports

#### Gemini Agent Integration
The core runtime should mount skill instructions and local tools into the workspace for the Gemini agent to use. The agent decides when to invoke them based on task context.

#### Requirements
- Skills must be installable without modifying core architecture.
- External publishing skills must require explicit user instruction in v1.

## Data Model and Persistence

### File-Based State
```text
data/sessions.json     # conversation_id -> gemini session metadata
data/dedup.json        # recent message ids for deduplication
data/schedules.json    # scheduled jobs
workspaces/*           # per-conversation state
```

### Persistence Rules
- All writes should be atomic where practical.
- JSON stores should tolerate process restarts.
- Corrupt state files should fail gracefully with operator-visible diagnostics.

## Execution Lifecycle

### Reactive Conversation
```text
User sends message in Feishu
-> Feishu Gateway receives event via WebSocket
-> Dispatcher deduplicates and normalizes
-> Gemini Agent Engine prepares workspace and prompt
-> Gemini CLI Agent runs with session restore
-> Agent optionally reads/writes memory or schedules via tools
-> Dispatcher appends log
-> Gateway returns Feishu card reply
```

### Proactive Scheduled Task
```text
Scheduler loop detects due task
-> Dispatcher creates synthetic incoming request
-> Gemini Agent Engine runs with same workspace/session model
-> Result sent to target Feishu chat
-> Scheduler updates or removes task
-> Execution logged
```

## Configuration
Use environment variables in `.env`.

Required:
```env
FEISHU_APP_ID=
FEISHU_APP_SECRET=
GEMINI_API_KEY=
GEMINI_CLI_PATH=gemini
BOT_NAME=GeminiBot
DEFAULT_TIMEZONE=Asia/Shanghai
WORKSPACE_ROOT=~/geminibot/workspaces
DATA_ROOT=~/geminibot/data
```

Optional:
```env
POLL_INTERVAL_SECONDS=30
RECENT_SUMMARY_DAYS=7
CARD_FOOTER_ENABLED=true
LOG_LEVEL=INFO
```

## Safety and Constraints
- Destructive external actions must be gated by explicit user intent.
- Publishing workflows should support dry-run mode first.
- Tool audit logs should be stored for debugging.
- The bot should identify itself consistently as the user’s personal assistant.
- Secrets must stay in environment variables or secure local config, never in workspace memory files.

## Observability
### Logs
- gateway events
- dispatcher decisions
- gemini subprocess invocation metadata
- scheduler triggers
- memory consolidation events

### Metrics to expose later
- message latency
- agent success rate
- schedule execution success rate
- memory tool usage counts

## Failure Handling
- If Gemini CLI invocation fails, return a user-friendly error card and log stderr.
- If memory consolidation fails, keep raw logs and skip rewrite.
- If a scheduled task fails, record failure and retry on the next valid run only if configured.
- If Feishu send fails, store unsent payload for inspection.

## Implementation Phases

### Phase 1: Core Chat Loop
- Feishu WebSocket gateway
- Dispatcher
- Gemini CLI adapter
- card rendering
- per-conversation workspace creation

### Phase 2: Durable Context
- file-backed session storage
- raw conversation logs
- MEMORY.md injection
- `/clear` and `/remember`

### Phase 3: Proactive Agent
- scheduler store and polling loop
- schedule/list/delete tools
- proactive task delivery to Feishu

### Phase 4: Skills
- pluggable skill directory
- content publishing skills
- explicit confirmation model for external posting

## Open Questions
- What exact CLI flags and session semantics does Gemini CLI Agent expose for resume/structured output?
- Should tool bridging use MCP directly, subprocess JSON-RPC, or a simpler stdin/stdout wrapper in v1?
- Should long-term memory be shared globally across all conversations, or remain per-conversation plus optional global profile?
- Do scheduled tasks need concurrency controls to prevent overlapping runs for long jobs?
- Should card rendering support streaming/partial updates later?

## Success Criteria
- A user can message the bot in Feishu and receive a contextual answer.
- The bot remembers explicit preferences across `/clear` and future sessions.
- A user can create a scheduled reminder in natural language and receive it later.
- All core state is visible under `~/geminibot` as readable files.
- The architecture is modular enough to add publishing skills without redesigning the core.

## Appendix: Mapping from the Article to This Design
- **Feishu gateway** stays the same in spirit: WebSocket, no public callback requirement.
- **Agent engine** swaps Claude Code CLI for Gemini CLI Agent behind a Python adapter.
- **Memory system** keeps the article’s file-first approach: daily logs, summaries, and long-term memory.
- **Scheduler** keeps cron + polling because it is simple and transparent.
- **Skill system** keeps the article’s idea of teaching the assistant specialized workflows, but adapts implementation to Python tooling around Gemini Agent.