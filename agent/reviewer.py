"""reviewer 子 agent：只读 + 只看 diff，只回最后一条消息。

价值不在"多一个角色"，在于主上下文只增加问题列表那几百字，
而它自己读掉的整份 diff 不回传。custom_report 里的 main_context_delta 就是这一行。
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .skills import estimate_tokens

CHECKLIST = [
    "改到不该改的文件",
    "新增依赖",
    "except: pass",
    "新分支没测试",
    "调试残留",
]

BAD_FILES = re.compile(r"(^|/)(\.env$|dist/|build/|node_modules/|.*\.lock$)")
DEP_LINE = re.compile(r"^\+.*(dependencies|requirements|install_requires)")
SWALLOW = re.compile(r"^\+.*except[^\n]*:\s*\n\+\s*(pass|\.\.\.)\s*$", re.M)
DEBUG = re.compile(r"^\+.*(\bprint\(|breakpoint\(|TODO|FIXME)", re.M)
BRANCH = re.compile(r"^\+\s*(if|elif)\b", re.M)


@dataclass
class Review:
    issues: list[str] = field(default_factory=list)
    diff_bytes: int = 0
    subagent_tokens: int = 0

    @property
    def context_delta(self) -> int:
        return len(self.message())

    def message(self) -> str:
        if not self.issues:
            return "reviewer：五条清单都过，没有发现问题。"
        return "reviewer 发现：\n" + "\n".join(f"- {x}" for x in self.issues)


def _git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20)
    except Exception:
        return ""
    return r.stdout if r.returncode == 0 else ""


def review(root: Path, diff: str | None = None) -> Review:
    """只读审查。diff 可以外部传入（测试用），否则现取 git diff。"""
    if diff is None:
        diff = _git(root, "diff", "--unified=0")
        if not diff.strip():
            diff = _git(root, "diff", "--cached", "--unified=0")

    out = Review(diff_bytes=len(diff.encode("utf-8")))
    out.subagent_tokens = estimate_tokens(diff)   # 子 agent 自己读掉的量

    # 1. 改到不该改的文件（工作区 + 暂存区都要看，git add 过就只剩 cached）
    changed = set(_git(root, "diff", "--name-only").split())
    changed |= set(_git(root, "diff", "--cached", "--name-only").split())
    for line in sorted(changed):
        if BAD_FILES.search(line):
            out.issues.append(f"[改到不该改的文件] {line}：配置/产物/依赖锁不该进本次改动")

    # 2/3/5. 依赖、吞异常、调试残留
    if DEP_LINE.search(diff):
        out.issues.append("[新增依赖] diff 里动了依赖声明：确认必需，并在报告里说明理由")
    for m in SWALLOW.finditer(diff):
        out.issues.append(f"[吞异常] {m.group(0).strip()[:70]}：改成抛错或至少记日志")
        break
    for m in DEBUG.finditer(diff):
        out.issues.append(f"[调试残留] {m.group(0).strip()[:70]}：提交前删掉")

    # 4. 新分支没测试
    if BRANCH.search(diff) and "test_" not in diff:
        out.issues.append("[新分支没测试] diff 新增了 if/elif，但没有对应的 test_ 改动")

    return out
