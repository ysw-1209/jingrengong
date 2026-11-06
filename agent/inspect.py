"""验收数字生成器：所有数字都由程序算出来，不接受手写。

    python -m agent.inspect skills --out skill_report.json
    python -m agent.inspect custom --out custom_report.json
    python -m agent.inspect daemon --out daemon_report.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .agents_md import load as load_agents_md
from .config import Settings
from .hooks import Hooks
from .reviewer import review
from .skills import catalog_text, estimate_tokens, load_skills, match_skill
from .tools import ToolResult, tool_edit

# 七条真话 + 两条不该触发。中文写法来自真实对话，不是术语。
TRIALS = [
    ("帮我跑一下测试", "run-tests"),
    ("测试挂了，为什么", "run-tests"),
    ("把这段 diff 审一遍", "review-diff"),
    ("review 一下我这次的改动", "review-diff"),
    ("看看这个 issue 说了什么", "fetch-issue"),
    ("抓一下 issue 1", "fetch-issue"),
    ("给新人讲讲这段代码怎么读", "explain-code"),
    ("逐行讲解一下 stats.py 的思路", "explain-code"),
    ("今天天气怎么样", None),
    ("帮我把这个函数改成异步", None),
]


# --------------------------------------------------------------------------- #
# 增量 A
# --------------------------------------------------------------------------- #
def skills_report(root: Path) -> dict:
    skills, errors = load_skills(root)
    catalog = catalog_text(skills, True)
    catalog_tokens = estimate_tokens(catalog)
    full_tokens = sum(estimate_tokens(s.full_text) for s in skills) or 1

    trials = []
    false_positive = 0
    for query, expected in TRIALS:
        hit, _score, _all = match_skill(query, skills)
        ok = hit == expected
        if not ok and expected is None:
            false_positive += 1
        trials.append({"query": query, "expected": expected, "hit": hit, "ok": ok})

    return {
        "skills": [
            {"name": s.name, "has_scripts": s.has_scripts, "uses_mcp": s.uses_mcp}
            for s in skills
        ],
        "catalog_tokens": catalog_tokens,
        "full_tokens": full_tokens,
        "disclosure_ratio": round(full_tokens / max(catalog_tokens, 1), 1),
        "trials": trials,
        "false_positive": false_positive,
        "load_errors": errors,
    }


# --------------------------------------------------------------------------- #
# 增量 B
# --------------------------------------------------------------------------- #
def custom_report(root: Path) -> dict:
    doc = load_agents_md(root)

    # 1) 两条拒绝：真的走一遍 hook 的 if
    probe = Hooks(Settings.from_env(root))
    probe.pre("write", {"path": ".env", "content": "OPENAI_API_KEY=sk-test"})
    probe.pre("bash", {"command": "rm -rf build/"})
    denied = [
        {"tool": d["tool"], **({"path": d["path"]} if "path" in d else {"command": d["command"]}),
         "reason": d["reason"] if "reason" in d else ""}
        for d in probe.stats.denied
    ]

    # 2) 自动测试：先故意改错 → 红 → 改对 → 绿（真实执行，不是模拟）
    tmp = Path(tempfile.mkdtemp(prefix="autotest_"))
    hooks = Hooks(Settings.from_env(tmp), enabled=True)
    try:
        for name in ("stats.py", "test_stats.py"):
            shutil.copy(root / "samples" / name, tmp / name)
        bug_line = next(l for l in (tmp / "stats.py").read_text(encoding="utf-8").splitlines()
                        if "BUG：" in l)

        r1 = tool_edit(tmp, "stats.py", bug_line, "        return 1  # 故意改错")
        hooks.post("edit", {"path": "stats.py"}, r1)

        r2 = tool_edit(tmp, "stats.py", "        return 1  # 故意改错",
                       '        raise ValueError("mean() 不接受空列表")')
        hooks.post("edit", {"path": "stats.py"}, r2)
        autotest = dict(hooks.stats.autotest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 3) reviewer：两次真实运行（一次有问题，一次干净）
    rv_tmp = Path(tempfile.mkdtemp(prefix="review_"))
    runs = 0
    issues_found = 0
    context_delta = 0
    subagent_tokens = 0
    try:
        _git(rv_tmp, "init", "-q")
        (rv_tmp / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
        (rv_tmp / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        _git(rv_tmp, "add", ".")
        _git(rv_tmp, "commit", "-qm", "base")

        # 制造 4 类问题：新增依赖 / 吞异常 / 调试残留 / 改到产物目录
        (rv_tmp / "pyproject.toml").write_text(
            "[project]\nname = 'x'\ndependencies = ['requests']\n", encoding="utf-8")
        (rv_tmp / "app.py").write_text(
            "def f():\n    try:\n        1 / 0\n    except Exception:\n        pass\n"
            "    print('debug')\n    return 1\n", encoding="utf-8")
        (rv_tmp / "dist").mkdir(exist_ok=True)
        (rv_tmp / "dist" / "app.bin").write_text("x", encoding="utf-8")
        _git(rv_tmp, "add", "-A")

        r1 = review(rv_tmp)
        runs += 1
        issues_found += len(r1.issues)
        context_delta = max(context_delta, r1.context_delta)
        subagent_tokens += r1.subagent_tokens

        _git(rv_tmp, "reset", "-q", "--hard")
        r2 = review(rv_tmp)
        runs += 1
        issues_found += len(r2.issues)
        context_delta = max(context_delta, r2.context_delta)
        subagent_tokens += r2.subagent_tokens
    finally:
        shutil.rmtree(rv_tmp, ignore_errors=True)

    return {
        "agents_md": {"found": doc.found, "bytes": doc.bytes, "layers": doc.layers},
        "denied": denied,
        "autotest": autotest,
        "reviewer": {"runs": runs, "issues_found": issues_found,
                     "main_context_delta": context_delta,
                     "subagent_tokens": subagent_tokens},
    }


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), "-c", "user.name=inspect",
                    "-c", "user.email=inspect@local", *args],
                   capture_output=True, text=True, timeout=20)


# --------------------------------------------------------------------------- #
# 增量 C（由 daemon 模块提供，这里只负责落盘）
# --------------------------------------------------------------------------- #
def daemon_report() -> dict:
    from .daemon import run_report
    return run_report()


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agent.inspect", description="生成验收 JSON")
    ap.add_argument("what", choices=["skills", "custom", "daemon"])
    ap.add_argument("--out", default=None, help="输出文件，默认打印到 stdout")
    ap.add_argument("--root", default=None, help="工作区，默认当前目录")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path.cwd()
    if args.what == "skills":
        data = skills_report(root)
    elif args.what == "custom":
        data = custom_report(root)
    else:
        data = daemon_report()

    text = json.dumps(data, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"已写入 {args.out}")
        print(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
