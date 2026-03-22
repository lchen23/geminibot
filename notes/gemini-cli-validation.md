# Gemini CLI Validation

## Environment
- CLI path: `/opt/homebrew/bin/gemini`
- Command help verified on 2026-03-22
- Headless mode uses `-p/--prompt`
- Structured output supports `--output-format json`
- Session resume supports `--resume latest`

## Validation Results

### 1. Single prompt execution
Command:
```bash
gemini -p "Reply with exactly OK" --output-format json
```

Observed behavior:
- exit code: `0`
- stdout is valid JSON
- returned fields include `session_id`, `response`, and `stats`
- `response` value was `OK`

### 2. Structured output mode
Observed JSON shape:
```json
{
  "session_id": "...",
  "response": "...",
  "stats": {
    "models": {},
    "tools": {},
    "files": {}
  }
}
```

Integration impact:
- Current adapter expectation of `response` and `session_id` is correct.
- `stats` is extra metadata and can be ignored safely for v1.

### 3. Resume/session mode
Validation workspace:
- `workspaces/gemini-cli-validation/`

Round 1:
- Prompt: `Remember this code word: ORBIT-42. Reply with only ACK.`
- Response: `ACK`
- Session id: `1f6178da-8fbd-489b-b920-34a6bc9be887`

Round 2:
- Prompt: `What code word did I ask you to remember? Reply with only the code word.`
- Command used `--resume latest`
- Response: `ORBIT-42`
- Session id remained `1f6178da-8fbd-489b-b920-34a6bc9be887`

Integration impact:
- `--resume latest` works in the same workspace.
- File-backed session metadata can keep `resume: latest` for v1.

### 4. cwd/workspace behavior
Command run with cwd:
- `workspaces/gemini-cli-validation/`

Prompt:
- `What is the name of the current working directory? Reply with only the directory name.`

Response:
- `gemini-cli-validation`

Integration impact:
- Running Gemini CLI with `cwd=workspace` behaves as expected.

## Notes
- stderr included a keychain warning and fallback notice:
  - `Using FileKeychain fallback for secure storage.`
- This did not prevent successful execution.
- Current adapter can tolerate stderr as long as stdout contains valid JSON and exit code is `0`.
