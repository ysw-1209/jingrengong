"""收集当前未提交的改动，压成一份 review 用的清单。

只读：不 add、不 commit、不改文件。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

MAX_TOTAL = 12000      # 总输出上限，防止把上下文挤爆
MAX_PER_FILE = 3000    # 单文件 diff 上限


def run(*args: str) -> str:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return "（这台机器没有 git）"
    except subprocess.TimeoutExpired:
        return "（git 超时）"
    if r.returncode != 0:
        return (r.stderr or r.stdout or "").strip()
    return r.stdout


def main() -> int:
    root = Path(__file__).resolve().parents[3]
    stat = run("-C", str(root), "diff", "--stat")
    if not stat.strip():
        print("没有未提交的改动（git diff --stat 为空）。")
        return 0

    print("## 变更清单")
    print(stat.strip())
    print()

    files = run("-C", str(root), "diff", "--name-only").split()
    total = 0
    for name in files:
        if total >= MAX_TOTAL:
            print(f"\n（还有 {len(files) - files.index(name)} 个文件未显示，总量已截断）")
            break
        diff = run("-C", str(root), "diff", "--", name)
        if len(diff) > MAX_PER_FILE:
            diff = diff[:MAX_PER_FILE] + f"\n…（本文件 diff 超过 {MAX_PER_FILE} 字符，已截断）"
        print(f"## {name}")
        print(diff.rstrip())
        print()
        total += len(diff)

    print("## 检查提示")
    print("- 新增依赖看 pyproject.toml / requirements 的改动")
    print("- 调试残留搜 print( / breakpoint() / TODO / FIXME")
    return 0


if __name__ == "__main__":
    sys.exit(main())
