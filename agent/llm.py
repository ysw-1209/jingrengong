"""OpenAI 兼容接口调用（流式）。

只依赖 requests：换 DeepSeek / 通义 / 智谱 / 本地 vLLM 都只改 .env 里的 base_url 和 model。
"""
from __future__ import annotations

import json
import threading
from typing import Iterator

import requests

from .config import Settings


class Cancelled(Exception):
    """用户点了中断。"""


def stream_chat(
    settings: Settings,
    messages: list[dict],
    tools: list[dict],
    cancel: threading.Event | None = None,
) -> Iterator[dict]:
    """产出事件：{"type":"content_delta"|"done"|"error"}。"""
    url = f"{settings.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    def post(with_usage: bool) -> requests.Response:
        p = dict(payload)
        if not with_usage:
            p.pop("stream_options", None)
        return requests.post(url, json=p, headers=headers, stream=True,
                             timeout=(10, settings.step_timeout))

    try:
        resp = post(True)
        if resp.status_code == 400:  # 部分厂商不认 stream_options，退一档重试
            resp.close()
            resp = post(False)
        resp.raise_for_status()
        resp.encoding = "utf-8"  # 不写这行，requests 会按 ISO-8859-1 解码，中文全变乱码
    except requests.HTTPError as e:
        body = e.response.text[:300] if e.response is not None else ""
        yield {"type": "error", "message": f"模型接口 {e.response.status_code if e.response else ''}：{body}"}
        return
    except requests.RequestException as e:
        yield {"type": "error", "message": f"连不上模型接口：{type(e).__name__}: {e}"}
        return

    content_parts: list[str] = []
    tool_calls: dict[int, dict] = {}
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    try:
        for raw in resp.iter_lines(decode_unicode=True):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if not raw or not raw.startswith("data:"):
                continue
            data = raw[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                if delta.get("content"):
                    content_parts.append(delta["content"])
                    yield {"type": "content_delta", "text": delta["content"]}
                for tc in delta.get("tool_calls", []) or []:
                    i = tc.get("index", 0)
                    entry = tool_calls.setdefault(
                        i, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        entry["id"] = tc["id"]
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        entry["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        entry["function"]["arguments"] += fn["arguments"]
    except Cancelled:
        yield {"type": "error", "message": "已中断（用户取消）", "cancelled": True}
        return
    finally:
        resp.close()

    message: dict = {"role": "assistant", "content": "".join(content_parts) or None}
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    yield {"type": "done", "message": message, "usage": usage}
