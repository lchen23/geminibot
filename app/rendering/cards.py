from __future__ import annotations


def build_markdown_reply(text: str, footer: str | None = None) -> dict:
    return {
        "schema": "2.0",
        "body": {
            "elements": [
                {"tag": "markdown", "content": text},
                *([{"tag": "hr"}, {"tag": "markdown", "content": f"*{footer}*"}] if footer else []),
            ]
        },
    }
