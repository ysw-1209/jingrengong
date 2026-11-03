"""智能体主循环：决策 → 动作 → 观察 → 再决策。

三件关键事：
1. 它是生成器，产出事件流——CLI 直接打印，后面的 FastAPI/WebSocket 直接转发，
   关掉前端用 curl -N 也能看到同一条流。
2. 三道护栏：步数上限、token 预算、整轮超时（写智能体最难的是让它停）。
3. 每一步都检查 cancel：点了中断就真的停下，不是转圈。

增量 A/B 接进来之后，一轮开始前会组装：
system prompt = 基础提示 + AGENTS.md（全局→项目）+ 技能目录（只有 name + description）
工具表 = 四个内置 + use_skill + MCP 工具
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Iterator

from .agents_md import load as load_agents_md
from .config import Settings
from .hooks import Hooks
from .llm import stream_chat
from .mcp import McpHub
from .skills import (USE_SKILL_SCHEMA, catalog_text, load_skills, render_skill)
from .tools import SCHEMAS, ToolResult, dispatch

SYSTEM_PROMPT = """你是一个在用户本机上干活的编程助手。

工作方式：先看清，再动手。不确定文件内容就先 read，不要凭记忆猜代码。
- 改代码优先用 edit（精确替换），只有新建或大改才用 write，禁止用 sed/echo 改文件。
- 搜索、跑测试、git、装依赖一律用 bash，并且给 timeout。
- 工具报错是线索，不是终点：读完错误信息换一条路再试，最多自救几次；
  反复失败就停下来说明原因，不要原地打转。
- 被 hook 拒绝时不要换个写法重试，按它给的替代方案做。
- 改完代码尽量跑一遍测试或脚本验证，再把结果告诉用户。
- 回答用简洁中文，说清楚你读了哪个文件、改了什么。"""


def _flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off"}


def run_agent(
    prompt: str,
    settings: Settings,
    history: list[dict] | None = None,
    cancel: threading.Event | None = None,
    hooks: Hooks | None = None,
    enable_skills: bool | None = None,
) -> Iterator[dict]:
    """跑一轮对话，产出事件。事件类型见 README / cli.py。"""
    skills_on = _flag("ENABLE_SKILLS") if enable_skills is None else enable_skills
    hooks = hooks or Hooks(settings, enabled=_flag("ENABLE_HOOKS"))

    skills, skill_errors = load_skills(settings.root) if skills_on else ([], [])
    agents_doc = load_agents_md(settings.root)

    parts = [SYSTEM_PROMPT]
    if agents_doc.found:
        parts.append(agents_doc.prompt())
    catalog = catalog_text(skills, enabled=skills_on)
    if catalog:
        parts.append(catalog)

    messages: list[dict] = [{"role": "system", "content": "\n\n".join(parts)}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": prompt})

    hub = McpHub(settings.root)
    hub.load()
    tools = list(SCHEMAS)
    if skills_on and skills:
        tools.append(USE_SKILL_SCHEMA)
    tools.extend(hub.schemas())

    yield {
        "type": "start",
        "model": settings.model,
        "root": str(settings.root),
        "skills": len(skills) if skills_on else 0,
        "agents_md": agents_doc.layers,
        "mcp_tools": len(hub.routes),
        "skill_errors": skill_errors,
    }

    steps = 0
    used_tokens = 0
    deadline = time.time() + settings.overall_timeout

    try:
        while True:
            if cancel is not None and cancel.is_set():
                yield {"type": "cancelled", "steps": steps}
                return
            if steps >= settings.max_steps:
                yield {"type": "error",
                       "message": f"达到步数上限（{settings.max_steps} 步）仍未结束，已停下",
                       "steps": steps}
                return
            if used_tokens > settings.token_budget:
                yield {"type": "error",
                       "message": f"超出 token 预算（{settings.token_budget}），已停下",
                       "steps": steps}
                return
            if time.time() > deadline:
                yield {"type": "error",
                       "message": f"超出总时长（{settings.overall_timeout:.0f}s），已停下",
                       "steps": steps}
                return

            steps += 1
            assistant_msg: dict | None = None
            for ev in stream_chat(settings, messages, tools, cancel):
                if ev["type"] == "content_delta":
                    yield ev
                elif ev["type"] == "error":
                    # 用户中断不等于出错：别让界面把它显示成"失败"
                    if ev.get("cancelled"):
                        yield {"type": "cancelled", "steps": steps}
                    else:
                        yield ev
                    return
                elif ev["type"] == "done":
                    assistant_msg = ev["message"]
                    used_tokens += ev.get("usage", {}).get("total_tokens", 0)

            if assistant_msg is None:
                yield {"type": "error", "message": "模型没有返回内容", "steps": steps}
                return
            messages.append(assistant_msg)

            calls = assistant_msg.get("tool_calls") or []
            if not calls:
                yield {"type": "done", "content": assistant_msg.get("content") or "",
                       "steps": steps, "tokens": used_tokens}
                return

            if assistant_msg.get("content"):
                yield {"type": "content_done"}

            for call in calls:
                if cancel is not None and cancel.is_set():
                    yield {"type": "cancelled", "steps": steps}
                    return
                name = call["function"]["name"]
                raw_args = call["function"].get("arguments") or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
                    result = ToolResult(f"工具参数不是合法 JSON：{raw_args}", is_error=True)
                else:
                    result = None

                yield {"type": "tool_call", "name": name, "args": args,
                       "call_id": call.get("id", "")}

                if result is None:
                    decision = hooks.pre(name, args)
                    if not decision.allowed:
                        result = ToolResult(decision.as_text(), is_error=True)
                        yield {"type": "hook_denied", "tool": name,
                               "reason": decision.reason,
                               "alternative": decision.alternative,
                               "call_id": call.get("id", "")}
                    elif name == "use_skill":
                        body = render_skill(str(args.get("name") or ""), skills)
                        result = ToolResult(body)
                        yield {"type": "skill_used", "name": args.get("name"),
                               "chars": len(body), "call_id": call.get("id", "")}
                    elif name.startswith("mcp__"):
                        text, is_error = hub.call(name, args)
                        result = ToolResult(text, is_error=is_error)
                    else:
                        result = dispatch(name, args, settings)

                    extra = hooks.post(name, args, result)
                    if extra:
                        result = ToolResult(f"{result.text}\n\n{extra}", result.images,
                                            result.is_error)

                yield {"type": "tool_result", "name": name, "text": result.text,
                       "is_error": result.is_error, "call_id": call.get("id", "")}

                messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                 "name": name, "content": result.text})
                if result.images:  # 图片单独走多模态内容块，不要塞进 text 字段
                    messages.append({"role": "user", "content": [
                        {"type": "text", "text": "上面 read 返回的图片如下："},
                        *[{"type": "image_url", "image_url": {"url": u}} for u in result.images],
                    ]})
    finally:
        hub.close()
