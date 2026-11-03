"""三条 hook：两条 PreToolUse（拦截）、一条 PostToolUse（自动测试回注）。

关键点：这是代码里的 if，不是 AGENTS.md 里的一句"禁止"。
模型可以不理提示词，但绕不过 before_tool()。
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .tools import ToolResult

SECRET_NAMES = {".env", ".env.local", ".env.production"}
SECRET_SUFFIX = {".pem", ".key", ".p12", ".pfx"}

DANGEROUS = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b"),
     "递归强制删除不可逆",
     "先跑 `git clean -nd` 看会删什么；确认后再 `git clean -fd`，或把文件移到 .trash/"),
    (re.compile(r"\bgit\s+push\s+(--force\b|-f\b)"),
     "强推会覆盖远端历史",
     "用 `git revert` 生成反向提交；确实要推就用 `--force-with-lease` 并在群里说明"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"),
     "硬重置会丢掉未提交的改动",
     "先 `git stash` 存起来；只想退暂存区用 `git restore --staged <file>`"),
]


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    alternative: str = ""

    def as_text(self) -> str:
        return (
            f"已被 hook 拒绝：{self.reason}。\n"
            f"这不是命令执行失败，是硬约束。请改用：{self.alternative}\n"
            "不要换个写法重试同一条命令。"
        )


@dataclass
class HookStats:
    denied: list[dict] = field(default_factory=list)
    autotest: dict = field(default_factory=lambda: {
        "runs": 0, "red": 0, "green": 0, "red_to_green": 0, "injected_lines": 0,
    })
    _last_red: bool = False


class Hooks:
    def __init__(self, settings: Settings, enabled: bool = True):
        self.settings = settings
        self.enabled = enabled
        self.stats = HookStats()

    # ---------------- PreToolUse ---------------- #
    def pre(self, name: str, args: dict) -> Decision:
        if not self.enabled:
            return Decision(True)

        if name in {"write", "edit"}:
            raw = str(args.get("path") or "")
            base = Path(raw).name.lower()
            if base in SECRET_NAMES or Path(base).suffix in SECRET_SUFFIX:
                d = Decision(
                    False,
                    f"拒绝写入密钥文件 {raw}：密钥不入库，公开仓库等于已泄露",
                    "把值放进环境变量或 .env（已在 .gitignore），仓库里只留 .env.example 里的 key 名",
                )
                self.stats.denied.append(
                    {"tool": name, "path": raw, "reason": "密钥不入库，用环境变量"})
                return d

        if name == "bash":
            cmd = str(args.get("command") or "")
            for pattern, reason, alternative in DANGEROUS:
                if pattern.search(cmd):
                    d = Decision(False, f"拒绝执行 `{cmd}`：{reason}", alternative)
                    self.stats.denied.append(
                        {"tool": "bash", "command": cmd, "reason": reason})
                    return d

        return Decision(True)

    # ---------------- PostToolUse ---------------- #
    def post(self, name: str, args: dict, result: ToolResult) -> str | None:
        """write / edit 成功后自动跑测试，只回注失败摘要（不是整段 traceback）。"""
        if not self.enabled or result.is_error or name not in {"write", "edit"}:
            return None

        command = self._test_command()
        if not command:
            return None

        self.stats.autotest["runs"] += 1
        try:
            proc = subprocess.run(
                command, shell=True, cwd=str(self.settings.root),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=min(self.settings.bash_timeout * 2, 120),
            )
        except Exception as e:
            return f"[自动测试] 没跑起来：{type(e).__name__}: {e}"

        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            self.stats.autotest["green"] += 1
            if self.stats._last_red:
                self.stats.autotest["red_to_green"] += 1
            self.stats._last_red = False
            return "[自动测试] 绿：测试通过。可以继续，但别把测试改掉。"

        self.stats.autotest["red"] += 1
        self.stats._last_red = True
        summary = self._failure_summary(output)
        self.stats.autotest["injected_lines"] += len(summary)
        return "[自动测试] 红：\n" + "\n".join(summary)

    # ---------------- 内部 ---------------- #
    def _test_command(self) -> str | None:
        root = self.settings.root
        py = "python" if not hasattr(sys, "_MEIPASS") else sys.executable
        if (root / "samples").is_dir() and any((root / "samples").glob("test_*.py")):
            return f'{py} -m unittest discover -s samples -p "test_*.py" -q'
        if any(root.glob("test_*.py")):
            return f'{py} -m unittest discover -s . -p "test_*.py" -q'
        if (root / "tests").is_dir():
            return f"{py} -m unittest discover -s tests -q"
        return None

    @staticmethod
    def _failure_summary(output: str, limit: int = 3) -> list[str]:
        """只取「哪个用例 / 期望 / 实际」，最多三行——80 行 traceback 会挤爆上下文。"""
        picked: list[str] = []
        for line in output.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith(("FAIL:", "ERROR:")) or s.startswith(("AssertionError", "ValueError",
                                                                  "TypeError", "KeyError")):
                picked.append(s[:160])
            if len(picked) >= limit:
                break
        if not picked:
            tail = [l.strip() for l in output.splitlines() if l.strip()][-limit:]
            picked = [t[:160] for t in tail]
        return picked or ["（测试失败，但没有可读的失败摘要）"]
