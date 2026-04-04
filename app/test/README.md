# GeminiBot Tests

This directory is the unified test root for GeminiBot.

Structure:
- `app/test/unit/` — fast isolated unit and wiring tests
- `app/test/e2e/` — end-to-end and manual validation flows

## Run tests

From the repo root:

### All tests

```bash
python -m unittest discover app/test
```

### Unit tests

```bash
python -m unittest discover app/test/unit
```

### E2E tests

```bash
python -m unittest discover app/test/e2e
```

### Manual E2E

```bash
python -m unittest app.test.e2e.test_manual_message_e2e
```

The manual suite prints the full validation plan first, then pauses on each step for you to send the exact message in Feishu.

## Manual E2E prerequisites

Before running the manual suite, make sure:

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

## Manual E2E coverage

The manual suite validates this sequence:

1. `/remember` persists memory and metadata
2. `/schedule once` writes a one-time task
3. `/schedule cron` writes a cron task
4. `/tasks` logs the current task list reply
5. `/delete-task` removes a seeded task
6. `/clear` regenerates the daily summary or summary-derived metadata

## Notes

- `discover app/test/e2e` includes both real E2E and manual E2E modules
- if `GEMINIBOT_E2E_REAL` or `GEMINIBOT_E2E_MANUAL` is not set, those suites skip by design
- the manual suite is ordered on purpose and preserves the intended step-by-step flow
