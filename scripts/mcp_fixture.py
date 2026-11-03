"""一个极小的 MCP server（stdio），用来演示技能编排 MCP。

不需要网络、不需要 token：issue 内容是固定的，评审换台机器也能复现。
"""
from __future__ import annotations

import json
import sys

ISSUES = {
    1: {
        "number": 1,
        "title": "mean([]) 应该报错，现在静默返回 0",
        "state": "open",
        "labels": ["bug", "stats"],
        "body": (
            "调用 mean([]) 时返回 0，导致上层把它当成合法结果写进了报表。\n"
            "期望：和 median([]) 一样抛 ValueError。\n"
            "复现：python -c \"from stats import mean; print(mean([]))\"\n"
            "影响：summary([]) 也会返回 n=0 的假数据。"
        ),
    },
    2: {
        "number": 2,
        "title": "测试没覆盖 median 的偶数长度分支",
        "state": "open",
        "labels": ["test"],
        "body": "median([1,2,3,4]) 没有被断言覆盖，建议补一个用例。",
    },
}

TOOLS = [{
    "name": "get_issue",
    "description": "按编号读取 issue 的标题、状态、标签与正文。",
    "inputSchema": {
        "type": "object",
        "properties": {"number": {"type": "integer", "description": "issue 编号，从 1 开始"}},
        "required": ["number"],
    },
}]


def reply(msg_id, result=None, error=None):
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

        if "id" not in msg:          # 通知，不回
            continue

        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "initialize":
            reply(msg["id"], {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "0.1"},
            })
        elif method == "tools/list":
            reply(msg["id"], {"tools": TOOLS})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name != "get_issue":
                reply(msg["id"], error=f"没有这个工具：{name}")
                continue
            number = int(args.get("number") or 1)
            issue = ISSUES.get(number)
            if issue is None:
                reply(msg["id"], {
                    "content": [{"type": "text", "text": f"没有编号为 {number} 的 issue"}],
                    "isError": True,
                })
            else:
                reply(msg["id"], {
                    "content": [{"type": "text",
                                 "text": json.dumps(issue, ensure_ascii=False, indent=2)}],
                    "isError": False,
                })
        else:
            reply(msg["id"], error=f"不支持的方法：{method}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
