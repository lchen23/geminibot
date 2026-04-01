from __future__ import annotations


def build_markdown_reply(text: str, footer: str | None = None, *, element_id: str | None = None) -> dict:
    element = {"tag": "markdown", "content": text}
    if element_id:
        element["element_id"] = element_id
    return {
        "schema": "2.0",
        "body": {
            "elements": [
                element,
                *([{"tag": "hr"}, {"tag": "markdown", "content": f"*{footer}*"}] if footer else []),
            ]
        },
    }


def build_streaming_markdown_card(text: str, *, summary: str, element_id: str) -> dict:
    return {
        "schema": "2.0",
        "config": {
            "streaming_mode": True,
            "summary": {"content": summary},
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": text,
                    "element_id": element_id,
                }
            ]
        },
    }
