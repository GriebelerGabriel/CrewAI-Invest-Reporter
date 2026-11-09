"""Helpers shared by the crews' prompt inputs and outputs."""

from __future__ import annotations

import json
import re


def to_json(value: object) -> str:
    """Compact JSON for prompts, keeping accents readable."""
    return json.dumps(value, ensure_ascii=False, indent=1)


def strip_code_fence(text: str) -> str:
    """LLMs sometimes wrap the whole answer in a ``` fence despite instructions."""
    stripped = text.strip()
    match = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*)\n```", stripped, flags=re.DOTALL)
    return match.group(1) if match else stripped
