"""一个只读的 git MCP server（stdio）。

为什么再写一个：证明 MCP 不止"一个假 server"，而是能同时接多个、
工具名带前缀聚合、技能可以编排其中任意一个。这里全是只读命令，
不会替你 commit / push。

被 agent/mcp.py 以子进程方式拉起，cwd 就是工作区根。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
MAX_CHARS = 4000

TOOLS = [
    {
        "name": "git_status",
        "description": "查看工作区未提交的改动（git status --short）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "git_diff",
        "description": "查看未提交改动的 diff（只读）。可选只看某个文件、或只看暂存区。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "只查看这个文件/目录"},
                "staged": {"type": "boolean", "description": "true 则看暂存区（--cached）"},
            },
        },
    },
    {
        "name": "git_log",
        "description": "最近几次提交（oneline），用来了解改动历史。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "条数，默认 10"}},
        },
    },
]


def run_git(args: list[str]) -> tuple[str, bool]:
    """返回 (文本, 是否出错)。空仓库跑 git log 会 returncode=128，不能当成功。"""
    try:
        r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=15)
    except FileNotFoundError:
        return "这台机器没有 git", True
    except subprocess.TimeoutExpired:
        return "git 超时（15s）", True
    text = (r.stdout or "") + (r.stderr or "")
    text = text.strip() or "（没有输出）"
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n…（超过 {MAX_CHARS} 字符已截断）"
    return text, r.returncode != 0


def call(name: str, args: dict) -> tuple[str, bool]:
    if name == "git_status":
        return run_git(["status", "--short"])
    if name == "git_diff":
        cmd = ["diff", "--unified=3"]
        if args.get("staged"):
            cmd.append("--cached")
        if args.get("path"):
            cmd += ["--", str(args["path"])]
        return run_git(cmd)
    if name == "git_log":
        limit = max(1, min(int(args.get("limit") or 10), 50))
        return run_git(["log", "--oneline", "-n", str(limit)])
    return f"没有这个工具：{name}", True


def reply(msg_id, result=None, error=None) -> None:
    payload = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = {"code": -32000, "message": str(error)}
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in msg:
            continue

        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "initialize":
            reply(msg["id"], {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "git", "version": "0.1"},
            })
        elif method == "tools/list":
            reply(msg["id"], {"tools": TOOLS})
        elif method == "tools/call":
            name = params.get("name")
            text, is_error = call(str(name), params.get("arguments") or {})
            reply(msg["id"], {
                "content": [{"type": "text", "text": text}],
                "isError": is_error,
            })
        else:
            reply(msg["id"], error=f"不支持的方法：{method}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
