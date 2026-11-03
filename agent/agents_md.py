"""AGENTS.md 两层注入：先全局，再项目。

纪律：它是地图，不是文档。超过 300 行说明写跑偏了，这里会直接截断并记录。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MAX_BYTES = 12_000        # ≈300 行，超了就只取前 12KB
MAX_TOTAL = 24_000


@dataclass
class AgentsDoc:
    found: bool = False
    layers: list[str] = field(default_factory=list)
    parts: list[tuple[str, str]] = field(default_factory=list)
    bytes: int = 0
    truncated: bool = False

    def prompt(self) -> str:
        if not self.found:
            return ""
        chunks = ["## 项目约定（AGENTS.md，必须遵守）"]
        for layer, text in self.parts:
            chunks.append(f"### {layer}\n{text.strip()}")
        return "\n\n".join(chunks)


def _read_limited(path: Path, doc: AgentsDoc) -> str:
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        doc.truncated = True
        raw = raw[:MAX_BYTES]
        text = raw.decode("utf-8", errors="replace") + "\n…（超过 12KB，已截断）"
    else:
        text = raw.decode("utf-8", errors="replace")
    doc.bytes += min(len(raw), MAX_BYTES)
    return text


def _find_project(root: Path, depth: int = 5) -> Path | None:
    cur = root.resolve()
    for _ in range(depth):
        cand = cur / "AGENTS.md"
        if cand.is_file():
            return cand
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def load(root: Path) -> AgentsDoc:
    doc = AgentsDoc()
    global_path = Path(os.environ.get("AGENT_GLOBAL_MD")
                       or Path.home() / ".desktop-agent" / "AGENTS.md")

    if global_path.is_file():
        doc.parts.append(("global", _read_limited(global_path, doc)))
        doc.layers.append("global")

    project = _find_project(root)
    if project is not None:
        doc.parts.append((f"project（{project.parent.name}）", _read_limited(project, doc)))
        doc.layers.append("project")

    doc.found = bool(doc.parts)
    if doc.bytes > MAX_TOTAL:
        doc.truncated = True
    return doc
