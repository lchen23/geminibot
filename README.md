# GeminiBot

A local-first Feishu AI assistant implemented in Python and designed to use Gemini CLI Agent as its reasoning runtime.

## Current Status
This repository currently contains:
- architecture spec
- implementation plan
- initial Python project skeleton

## Next Steps
1. Fill in Feishu gateway integration
2. Finalize Gemini CLI adapter behavior
3. Implement memory and scheduler tool bridges

## Run
```bash
python -m app.main
```

## Acceptance

Install the project in editable mode if you want the script entry to be available directly:

```bash
pip install -e .
```

Re-run the natural-language acceptance for the memory and scheduler tool bridge with the packaged command:

```bash
geminibot-tool-bridge-acceptance
```

If you are running directly from the repository without installing the script entry, use:

```bash
python3 run_tool_bridge_acceptance.py
```

Useful variants:

```bash
geminibot-tool-bridge-acceptance --only memory
geminibot-tool-bridge-acceptance --only scheduler
geminibot-tool-bridge-acceptance --print-raw-output
```

Acceptance artifacts are written to:
- `workspaces/<conversation_id>/acceptance_report.json`
- `notes/tool-bridge-acceptance.md`
- `workspaces/<conversation_id>/tool_audit.jsonl`
- `workspaces/<conversation_id>/MEMORY.md`
- `data/schedules.json`
