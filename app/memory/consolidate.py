from __future__ import annotations

from datetime import date
from pathlib import Path


def consolidate_workspace_memory(workspace: Path) -> None:
    logs_dir = workspace / "logs"
    if not logs_dir.exists():
        return

    summaries_dir = workspace / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summary_lines: list[str] = []
    memory_points: list[str] = []

    for log_file in sorted(logs_dir.glob("*.md")):
        content = log_file.read_text(encoding="utf-8").strip()
        if not content:
            continue

        line_count = len([line for line in content.splitlines() if line.strip()])
        summary_lines.append(f"## {log_file.stem}\n- Captured {line_count} non-empty log lines.")
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("**Q:**") or stripped.startswith("**A:**"):
                memory_points.append(stripped)

    if summary_lines:
        summary_file = summaries_dir / f"{date.today().isoformat()}.md"
        summary_file.write_text("\n\n".join(summary_lines).rstrip() + "\n", encoding="utf-8")

    memory_file = workspace / "MEMORY.md"
    existing = memory_file.read_text(encoding="utf-8") if memory_file.exists() else "# Memory\n"
    base_lines = [line[2:].strip() for line in existing.splitlines() if line.startswith("- ")]

    combined: list[str] = []
    seen: set[str] = set()
    for line in [*base_lines, *memory_points]:
        cleaned = line.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        combined.append(cleaned)

    body = "\n".join(f"- {line}" for line in combined)
    memory_file.write_text(f"# Memory\n\n{body}\n" if body else "# Memory\n", encoding="utf-8")
