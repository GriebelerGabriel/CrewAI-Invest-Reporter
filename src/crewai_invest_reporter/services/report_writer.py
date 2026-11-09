from __future__ import annotations

import re
from pathlib import Path

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")


class ReportWriter:
    """Persist markdown reports under a single reports directory."""

    def __init__(self, reports_dir: Path) -> None:
        self._reports_dir = reports_dir

    def path_for(self, name: str) -> Path:
        if not _SAFE_NAME.match(name):
            raise ValueError(f"Unsafe report name: {name!r}")
        return self._reports_dir / f"{name}.md"

    def write(self, name: str, content: str) -> Path:
        path = self.path_for(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(strip_code_fence(content).strip() + "\n", encoding="utf-8")
        return path


def strip_code_fence(text: str) -> str:
    """LLMs sometimes wrap the whole markdown answer in a ``` fence despite instructions."""
    stripped = text.strip()
    match = re.fullmatch(r"```(?:markdown|md)?\s*\n(.*)\n```", stripped, flags=re.DOTALL)
    return match.group(1) if match else stripped
