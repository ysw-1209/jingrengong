"""10 秒冒烟：不连模型，只验"零件还在不在"。

    python -m agent.selftest

任何一项红了就先修，不要往下做增量——增量是加法，不是补锅。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from .config import Settings
from .hooks import Hooks
from .mcp import McpHub
from .skills import catalog_text, estimate_tokens, load_skills
from .tools import ToolResult, dispatch


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="selftest_"))
    settings = Settings.from_env(tmp)
    results: list[tuple[str, bool, str]] = []

    # 1) 四个工具
    try:
        w = dispatch("write", {"path": "a/b.txt", "content": "hello\nworld\n"}, settings)
        r = dispatch("read", {"path": "a/b.txt"}, settings)
        e = dispatch("edit", {"path": "a/b.txt", "old": "world", "new": "agent"}, settings)
        b = dispatch("bash", {"command": "echo ok", "timeout": 10}, settings)
        ok = (not w.is_error and "1\t" in r.text and "已修改" in e.text
              and "[exit_code] 0" in b.text)
        results.append(("四个工具", ok, "read/write/edit/bash"))
    except Exception as ex:
        results.append(("四个工具", False, str(ex)))

    # 2) 技能：≥3 且三种形态齐
    try:
        skills, errors = load_skills(Path.cwd())
        forms = {("scripts" if s.has_scripts else "") + ("mcp" if s.uses_mcp else "") or "prompt"
                 for s in skills}
        ratio = estimate_tokens("".join(s.full_text for s in skills)) / max(
            estimate_tokens(catalog_text(skills)), 1)
        ok = len(skills) >= 3 and {"prompt", "scripts", "mcp"} <= forms and ratio >= 5
        results.append(("技能系统", ok, f"{len(skills)} 个 / ratio {ratio:.1f} / {sorted(forms)}"))
    except Exception as ex:
        results.append(("技能系统", False, str(ex)))

    # 3) hooks：.env 必须被拒
    try:
        h = Hooks(settings)
        d = h.pre("write", {"path": ".env", "content": "K=v"})
        d2 = h.pre("bash", {"command": "rm -rf build/"})
        ok = (not d.allowed) and bool(d.alternative) and (not d2.allowed)
        results.append(("hooks", ok, "拒绝 .env / rm -rf 且给替代方案"))
    except Exception as ex:
        results.append(("hooks", False, str(ex)))

    # 4) MCP：工具能列出来
    try:
        hub = McpHub(Path.cwd())
        hub.load()
        ok = len(hub.schemas()) >= 1
        results.append(("MCP", ok, f"{len(hub.schemas())} 个工具"))
        hub.close()
    except Exception as ex:
        results.append(("MCP", False, str(ex)))

    # 5) 自动测试回注（write 之后真的跑一次）
    try:
        (tmp / "test_demo.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n"
            "    def test_ok(self):\n        self.assertEqual(1, 1)\n", encoding="utf-8")
        h = Hooks(settings)
        extra = h.post("write", {"path": "x.txt"}, ToolResult("已写入 x.txt（1 行）"))
        ok = bool(extra) and "自动测试" in extra
        results.append(("自动测试回注", ok, (extra or "")[:40]))
    except Exception as ex:
        results.append(("自动测试回注", False, str(ex)))

    shutil.rmtree(tmp, ignore_errors=True)

    print("=== selftest ===")
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
