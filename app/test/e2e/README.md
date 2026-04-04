# Manual Feishu Message E2E

This document describes the manual Feishu message E2E flow in `app/test/e2e/test_manual_message_e2e.py`.

These checks are intentionally human-in-the-loop:
- the test process prints the exact message to send in Feishu
- you send that message manually in the target chat
- the test waits for local artifacts to confirm the bot handled it correctly

## Coverage

The manual suite validates this sequence:

1. `/remember` persists memory and metadata
2. `/schedule once` writes a one-time task
3. `/schedule cron` writes a cron task
4. `/tasks` logs the current task list reply
5. `/delete-task` removes a seeded task
6. `/clear` regenerates the daily summary or summary-derived metadata

## Prerequisites

Before running the suite, make sure:

- the GeminiBot service is already running and connected to Feishu
- the target Feishu chat is the same one configured for this test
- the repo `.env` exists, or equivalent environment variables are exported
- manual E2E is enabled with `GEMINIBOT_E2E_MANUAL=1`

Required manual E2E settings:

```env
GEMINIBOT_E2E_MANUAL=1
GEMINIBOT_E2E_FEISHU_CHAT_ID=<target chat id>
GEMINIBOT_E2E_CONVERSATION_ID=<conversation id, usually same as chat id>
```

Optional but useful:

```env
GEMINIBOT_E2E_FEISHU_USER_ID=<user id for seeded tasks>
WORKSPACE_ROOT=~/geminibot/workspaces
DATA_ROOT=~/geminibot/data
```

The suite also reads normal app settings such as `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `AI_PROVIDER`, `GEMINI_CLI_PATH`, and `CLAUDE_CLI_PATH` through the existing app config.

## Run the full guided flow

From the repo root:

```bash
python -m unittest app.test.e2e.test_manual_message_e2e
```

Or run all E2E tests under the unified directory:

```bash
python -m unittest discover app/test/e2e
```

What happens:
- unittest prints the full validation plan first for the manual suite
- each step prints the exact Feishu message to send
- after you send it, the process polls local files until the expected condition is met
- on success it automatically advances to the next step
- any tasks created during the run are cleaned up by the test case teardown

## Expected operator workflow

For each printed step:

1. read the step title and the exact message
2. paste that message into the configured Feishu chat
3. wait for the local assertion to pass
4. continue until the runner finishes

If the process times out, check:
- the bot service is still running
- you sent the message into the configured chat
- `WORKSPACE_ROOT` and `DATA_ROOT` point to the same storage used by the running bot
- today's log, memory, summary, or `schedules.json` is being written where the test expects

## Notes

- default wait timeout is 180 seconds per step
- the `/clear` validation uses a 240 second timeout because summary generation can take longer
- the suite is ordered on purpose; the unittest loader preserves the intended manual flow
