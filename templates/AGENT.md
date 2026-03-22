# Agent Checklist
1. Load persona files before responding.
2. Read long-term memory before major decisions.
3. Check recent summaries for near-term context.
4. Prefer safe, reversible actions.
5. Explain blockers clearly when they happen.
6. When memory or schedule operations are needed, use `python tools/tool_bridge.py ...` from the workspace shell.
7. Check `tools/README.md` for supported tool commands and argument schemas before invoking them.
8. Tool invocations are audited in `tool_audit.jsonl`; keep them intentional and relevant.
