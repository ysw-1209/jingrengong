"""终端入口：不依赖任何界面，能跑通就说明循环没和 UI 缠在一起。

    python -m agent.cli "读一下 samples/stats.py，把 mean 对空列表的处理改掉"
    python -m agent.cli                       # 多轮对话
    python -m agent.cli --trace docs/trace.md --root samples "跑一下测试"
"""
from __future__ import annotations

import argparse
import json
import queue
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import Settings
from .loop import run_agent

PROMPT = "说点什么…（直接回车发送，Ctrl+C 中断当前任务，exit 退出）\n> "


def _worker(q: queue.Queue, gen):
    try:
        for ev in gen:
            q.put(ev)
    except Exception as e:  # 兜底：循环里任何意外都不能静默
        q.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
    finally:
        q.put({"type": "__end__"})


def _print_event(ev: dict, max_chars: int = 3000) -> None:
    t = ev["type"]
    if t == "start":
        print(f"[启动] model={ev['model']} root={ev['root']}", flush=True)
    elif t == "content_delta":
        print(ev["text"], end="", flush=True)
    elif t == "content_done":
        print(flush=True)
    elif t == "tool_call":
        args = ev["args"]
        brief = ", ".join(f"{k}={v if len(str(v)) < 60 else str(v)[:60] + '…'}" for k, v in args.items())
        print(f"\n[工具] {ev['name']}({brief})", flush=True)
    elif t == "tool_result":
        mark = "[失败]" if ev["is_error"] else "[结果]"
        text = ev["text"]
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n…（终端显示截断，共 {len(ev['text'])} 字符）"
        print(f"{mark} {text}", flush=True)
    elif t == "done":
        print(f"\n[完成] 步数 {ev['steps']} · tokens {ev.get('tokens', 0)}", flush=True)
    elif t == "cancelled":
        print(f"\n[已中断] 第 {ev['steps']} 步停下，没有继续跑", flush=True)
    elif t == "error":
        print(f"\n[错误] {ev['message']}", flush=True)


def _run_once(prompt: str, settings: Settings, history: list[dict],
              trace_path: Path | None) -> None:
    started = time.time()
    q: queue.Queue = queue.Queue()
    cancel = threading.Event()
    events: list[dict] = []

    th = threading.Thread(target=_worker, args=(q, run_agent(prompt, settings, history, cancel)),
                          daemon=True)
    th.start()
    try:
        while True:
            try:
                ev = q.get(timeout=0.2)
            except queue.Empty:
                if not th.is_alive():
                    break
                continue
            if ev["type"] == "__end__":
                break
            events.append(ev)
            _print_event(ev)
    except KeyboardInterrupt:
        cancel.set()  # 点了中断就真停：先把事件排空，再退出
        print("\n[收到中断] 正在停止…", flush=True)
        deadline = 0
        while th.is_alive() and deadline < 30:
            th.join(timeout=0.5)
            deadline += 1
            try:
                ev = q.get_nowait()
            except queue.Empty:
                continue
            if ev["type"] == "__end__":
                break
            events.append(ev)
            _print_event(ev)
    th.join(timeout=2)

    if trace_path:
        _write_trace(trace_path, prompt, events, settings, time.time() - started)


def _brief(name: str, text: str, is_error: bool) -> tuple[str, bool]:
    """把工具返回压成一句话，并判断这次算不算失败。

    注意：bash 的非 0 退出码在工具层是"正常返回"（不抛错），
    但从"这一轮干成没干成"看它就是失败，trace 里必须如实标出来。
    """
    if is_error:
        first = (text or "失败").strip().splitlines()[0]
        return f"失败：{first[:52]}", True
    if name == "bash":
        m = re.search(r"\[exit_code\]\s*(-?\d+)", text or "")
        if m:
            return f"exit {m.group(1)}", m.group(1) != "0"
        return "完成", False
    if name == "read":
        m = re.search(r"共 (\d+) 行", text or "")
        return (f"{m.group(1)} 行" if m else "完成"), False
    if name == "write":
        m = re.search(r"(\d+) 行", text or "")
        return (f"{m.group(1)} 行" if m else "完成"), False
    if name == "edit":
        return ("1 处" if "已修改" in (text or "") else "完成"), False
    return "完成", False


def _write_trace(path: Path, prompt: str, events: list[dict], settings: Settings,
                 duration: float = 0.0) -> None:
    """把事件流整理成一份能直接交的验收材料：概览 → 步骤表 → 明细 → 结论。"""
    steps: list[dict] = []
    waiting: list[dict] = []
    final = ""
    used_steps = 0
    status = "完成"
    error_msg = ""

    for ev in events:
        t = ev["type"]
        if t == "tool_call":
            rec = {"no": len(steps) + 1, "name": ev["name"], "args": ev["args"],
                   "text": "", "brief": "（未返回）", "failed": False}
            steps.append(rec)
            waiting.append(rec)
        elif t == "tool_result":
            rec = waiting.pop(0) if waiting else (steps[-1] if steps else None)
            if rec is None:
                continue
            rec["text"] = ev["text"]
            rec["brief"], rec["failed"] = _brief(ev["name"], ev["text"], ev["is_error"])
        elif t == "done":
            final = ev.get("content") or ""
            used_steps = ev.get("steps") or used_steps
            status = "完成"
        elif t == "cancelled":
            status = f"中断（第 {ev.get('steps', 0)} 步）"
        elif t == "error":
            status = "失败"
            error_msg = ev.get("message", "")

    recovered = []
    for i, failed_step in enumerate(steps):
        if not failed_step["failed"]:
            continue
        for later in steps[i + 1:]:
            if later["name"] == failed_step["name"] and not later["failed"]:
                recovered.append((failed_step, later))
                break

    counts: dict[str, int] = {}
    for s in steps:
        counts[s["name"]] = counts.get(s["name"], 0) + 1
    count_text = "、".join(f"{k} × {v}" for k, v in counts.items()) or "无"

    lines = [
        "# 运行记录（trace）",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 时间 | {datetime.now():%Y-%m-%d %H:%M:%S} |",
        f"| 模型 | {settings.model} |",
        f"| 工作区 | `{settings.root}` |",
        f"| 耗时 | {duration:.1f} s |",
        f"| 步数 | {used_steps} |",
        f"| 工具调用 | {count_text} |",
        f"| 结果 | {status} |",
        f"| 失败与恢复 | {len(recovered)} 次 |",
        "",
        "## 用户输入",
        "",
        "```",
        prompt,
        "```",
        "",
        "## 步骤",
        "",
        "| # | 工具 | 参数 | 结果 |",
        "|---|---|---|---|",
    ]

    for s in steps:
        args = json.dumps(s["args"], ensure_ascii=False).replace("|", "\\|")
        if len(args) > 70:
            args = args[:70] + "…"
        lines.append(f"| {s['no']} | `{s['name']}` | `{args}` | {s['brief']} |")
    if not steps:
        lines.append("| — | — | — | 本轮没有调用工具 |")

    lines += ["", "## 过程明细", ""]
    for s in steps:
        lines += [f"### {s['no']}. {s['name']} —— {s['brief']}", "", "参数：", ""]
        lines.append("```json")
        lines.append(json.dumps(s["args"], ensure_ascii=False, indent=2))
        lines.append("```")
        if s["text"]:
            lines += ["", "返回：", "", "```"]
            lines += s["text"].splitlines()[:40]
            lines.append("```")
        lines.append("")

    lines += ["## 结论", "", f"- 共 {len(steps)} 次工具调用：{count_text}"]
    if recovered:
        for failed_step, later in recovered:
            lines.append(
                f"- 第 {failed_step['no']} 步 `{failed_step['name']}` 失败"
                f"（{failed_step['brief']}），第 {later['no']} 步重跑成功"
                f"（{later['brief']}）：失败 → 读错误 → 改代码 → 再验证，闭环成立"
            )
    else:
        lines.append("- 本轮没有出现「失败后重试成功」的情况")
    if error_msg:
        lines.append(f"- 出错信息：{error_msg}")
    if final:
        lines += ["", "最终回答：", "", "```", final, "```"]
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[trace] 已写入 {path}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="桌面智能体 · 后端命令行（第 1 步：脱离界面跑通）")
    ap.add_argument("prompt", nargs="?", help="一次性任务；不给就进入多轮对话")
    ap.add_argument("--root", default=None, help="工具可操作的目录，默认当前目录")
    ap.add_argument("--trace", default=None, help="把完整运行记录写成 markdown，如 docs/trace.md")
    args = ap.parse_args(argv)

    settings = Settings.from_env(args.root)
    if not settings.api_key:
        print("缺少 API Key：把 .env.example 复制成 .env，填入 OPENAI_API_KEY 后再跑。", file=sys.stderr)
        return 1
    settings.root.mkdir(parents=True, exist_ok=True)

    history: list[dict] = []
    trace_path = Path(args.trace) if args.trace else None

    if args.prompt:
        _run_once(args.prompt, settings, history, trace_path)
        return 0

    print(f"工作区：{settings.root}　模型：{settings.model}")
    while True:
        try:
            user = input(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return 0
        if not user:
            continue
        if user.lower() in {"exit", "quit", "退出"}:
            return 0
        _run_once(user, settings, history, trace_path)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
