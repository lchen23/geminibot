# GeminiBot

**A local-first Feishu AI assistant powered by Gemini CLI or Claude Code.**

GeminiBot brings an AI assistant directly into Feishu while keeping its runtime, memory, logs, and scheduled tasks on your own machine. It is designed for people who want a practical chat assistant with local control instead of a hosted bot platform.

## Why GeminiBot

GeminiBot is built for a simple workflow:
- chat with an AI assistant inside Feishu
- save lightweight memory within a conversation
- schedule reminders and recurring tasks
- keep the runtime and data local
- choose between **Gemini CLI** and **Claude Code**

If you want a small, hackable, local-first Feishu bot instead of an infrastructure-heavy system, this project is for you.

## Features

- **Feishu-native chat experience**  
  Receive messages from Feishu and reply back in the same chat.

- **Local-first runtime**  
  Workspaces, logs, memory, schedules, and runtime state are stored locally.

- **Choice of model runtime**  
  Run GeminiBot with either `gemini` or `claude`.

- **Conversation memory**  
  Save durable notes for a conversation with a simple command.

- **Built-in scheduling**  
  Create one-time reminders or recurring cron tasks from chat.

- **Streaming replies**  
  Responses can be delivered as progressively updated Feishu cards.

- **Simple operational model**  
  Start, stop, restart, and inspect the service with a small CLI.

## Demo commands

Use these directly inside Feishu:

```text
/help
/remember Prefer concise answers first
/schedule once | 2026-04-03T09:00:00 | Remind me about the morning standup
/schedule cron | 0 10 * * 1-5 | Remind me to check the daily report
/tasks
```

## Quick start

### Requirements

- Python 3.11+
- A supported reasoning CLI available on `PATH`
  - `gemini`
  - or `claude`
- A Feishu app with credentials

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Configure

Copy the example environment file:

```bash
cp .env.example .env
```

Minimum configuration:

```env
FEISHU_APP_ID=your_feishu_app_id
FEISHU_APP_SECRET=your_feishu_app_secret
AI_PROVIDER=gemini
GEMINI_CLI_PATH=gemini
CLAUDE_CLI_PATH=claude
GEMINI_APPROVAL_MODE=yolo
CLAUDE_PERMISSION_MODE=auto
```

Provider-specific approval settings:

- `GEMINI_APPROVAL_MODE` applies only when `AI_PROVIDER=gemini` and maps to Gemini CLI `--approval-mode`
- `CLAUDE_PERMISSION_MODE` applies only when `AI_PROVIDER=claude` and maps to Claude CLI `--permission-mode`

Useful optional settings:

```env
BOT_NAME=GeminiBot
DEFAULT_TIMEZONE=America/Los_Angeles
APP_ROOT=~/geminibot
POLL_INTERVAL_SECONDS=30
LOG_LEVEL=INFO
```

### Run

Start in the background:

```bash
geminibot start
```

Run in the foreground:

```bash
geminibot start --foreground
```

Check status:

```bash
geminibot status
```

Stop the service:

```bash
geminibot stop
```

Restart the service:

```bash
geminibot restart
```

## How it works

GeminiBot follows a straightforward runtime model:

1. load configuration from `.env`
2. verify Feishu credentials and selected CLI availability
3. connect to Feishu and receive messages
4. handle built-in commands directly
5. send normal messages to Gemini CLI or Claude Code
6. store memory, logs, schedules, and runtime data locally

That makes the project easy to run, inspect, and extend.

## Chat commands

### General

- `/help` — show the command list
- `/clear` — clear current conversation context and consolidate memory
- `/remember <text>` — save a memory note for the current conversation

### Scheduling

- `/tasks` — list scheduled tasks for the current chat
- `/schedule <once|cron> | <time-or-cron> | <prompt>` — create a scheduled task
- `/delete-task <task_id>` — delete a scheduled task

### Examples

Save a preference:

```text
/remember Reply in English unless I ask otherwise
```

Create a one-time reminder:

```text
/schedule once | 2026-04-03T15:00:00 | Remind me to submit the weekly update
```

Create a recurring reminder:

```text
/schedule cron | 0 18 * * 5 | Remind me every Friday at 18:00 to write the weekly report
```

## Data layout

By default, GeminiBot stores data in:

- workspace state: `~/geminibot/workspaces`
- shared app data: `~/geminibot/data`

This typically includes:
- conversation memory files
- daily chat logs
- schedules and execution history
- runtime logs and PID files

## Common service commands

```bash
geminibot start
geminibot start --foreground
geminibot status
geminibot stop
geminibot restart
```

## Testing

See `app/test/README.md` for the full test command matrix, including all tests, unit, E2E, and manual E2E.

Quick start from the repo root:

```bash
python -m unittest discover app/test/unit
python -m unittest discover app/test/e2e
python -m unittest app.test.e2e.test_manual_message_e2e
```

## Troubleshooting

### CLI not found

Check:
- `AI_PROVIDER` in `.env`
- `GEMINI_CLI_PATH` or `CLAUDE_CLI_PATH`
- whether `gemini` or `claude` runs successfully in your shell

### No reply appears in Feishu

Check:
- `FEISHU_APP_ID` and `FEISHU_APP_SECRET`
- whether the service is running: `geminibot status`
- application logs for startup or delivery errors

### Scheduled task does not run

Check:
- whether the service is still running
- whether `DEFAULT_TIMEZONE` matches your expectation
- whether the task appears in `/tasks`

## Project structure

Useful entry points if you want to explore the code:

- CLI entry: `app/cli.py`
- service startup: `app/main.py`
- dispatch and command handling: `app/dispatcher.py`
- Feishu gateway: `app/gateway/feishu.py`
- scheduler: `app/scheduler/`
- config loading: `app/config.py`

## Who this is for

GeminiBot is a good fit if you want:
- a Feishu bot that stays under your control
- a local-first assistant with simple persistence
- lightweight scheduling and memory features
- a small Python codebase that is easy to understand and modify

It is less about enterprise bot administration, and more about a practical personal or team assistant you can run yourself.