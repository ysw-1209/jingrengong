"""极简 MCP 客户端（stdio / JSON-RPC 2.0）。

只做三件事：initialize、tools/list、tools/call。
够用来证明「技能能编排你已经接好的 MCP」，不追求把协议实现全。
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path


class McpError(Exception):
    pass


class McpServer:
    def __init__(self, name: str, command: str, args: list[str], cwd: Path,
                 env_extra: dict | None = None):
        self.name = name
        if command in {"python", "python3"}:
            command = sys.executable          # 打包后没有 PATH 里的 python
        else:
            command = shutil.which(command) or command
        # Windows 上 npx / uvx 是 .CMD，Popen 不能直接执行，要用 cmd /c 包一层
        if os.name == "nt" and command.lower().endswith((".cmd", ".bat")):
            command, args = "cmd", ["/c", command, *args]
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        if env_extra:
            # 第三方 server 常要 token，写在 mcp.json 的 env 里，不落进代码
            env.update({str(k): str(v) for k, v in env_extra.items()})
        self.proc = subprocess.Popen(
            [command, *args],
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        self._id = 0
        self.tools: list[dict] = []

    # ---- 协议 ---------------------------------------------------------- #
    def _send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _read(self, timeout: float) -> dict:
        assert self.proc.stdout is not None
        box: queue.Queue = queue.Queue()

        def reader() -> None:
            try:
                box.put(self.proc.stdout.readline())
            except Exception as e:  # 进程死了也要把异常带回来
                box.put(e)

        threading.Thread(target=reader, daemon=True).start()
        try:
            line = box.get(timeout=timeout)
        except queue.Empty:
            raise McpError(f"MCP[{self.name}] 超时（{timeout}s）")
        if isinstance(line, Exception):
            raise McpError(f"MCP[{self.name}] 读取失败：{line}")
        if not line:
            raise McpError(f"MCP[{self.name}] 已退出，没有响应")
        try:
            return json.loads(line)
        except json.JSONDecodeError as e:
            raise McpError(f"MCP[{self.name}] 返回的不是 JSON：{e}")

    def request(self, method: str, params: dict, timeout: float = 8.0) -> dict:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            msg = self._read(timeout)
            if msg.get("id") != rid:
                continue  # 跳过通知 / 别的响应
            if "error" in msg:
                raise McpError(msg["error"].get("message") or "MCP 返回错误")
            return msg.get("result") or {}

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ---- 对外 ---------------------------------------------------------- #
    def start(self, timeout: float = 60.0) -> None:
        # 首次 npx 要下载包，8 秒不够；握手给 60 秒，之后调用再收紧
        self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "desktop-agent", "version": "1.1"},
        }, timeout=timeout)
        self.notify("notifications/initialized")
        result = self.request("tools/list", {}, timeout=timeout)
        self.tools = result.get("tools") or []

    def call(self, tool: str, args: dict) -> tuple[str, bool]:
        result = self.request("tools/call", {"name": tool, "arguments": args}, timeout=15.0)
        chunks = [c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text"]
        text = "\n".join(x for x in chunks if x) or "（MCP 没有返回文本）"
        return text, bool(result.get("isError"))

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass


class McpHub:
    """把 mcp.json 里的多个 server 聚合成一组工具：mcp__<server>__<tool>。"""

    def __init__(self, root: Path):
        self.root = root
        self.servers: dict[str, McpServer] = {}
        self.routes: dict[str, tuple[str, str]] = {}   # 全名 -> (server, tool)
        self.errors: list[str] = []

    def load(self, config_path: Path | None = None) -> None:
        path = config_path or (self.root / "mcp.json")
        if not path.exists():
            self.errors.append(f"没有 mcp.json：{path}")
            return
        try:
            conf = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            self.errors.append(f"mcp.json 解析失败：{e}")
            return

        for name, spec in (conf.get("servers") or {}).items():
            try:
                srv = McpServer(name, spec.get("command", "python"),
                                list(spec.get("args") or []), self.root,
                                env_extra=spec.get("env"))
                srv.start()
            except Exception as e:
                self.errors.append(f"MCP[{name}] 启动失败：{e}")
                continue
            self.servers[name] = srv
            for tool in srv.tools:
                full = f"mcp__{name}__{tool['name']}"
                self.routes[full] = (name, tool["name"])

    def schemas(self) -> list[dict]:
        out: list[dict] = []
        for srv in self.servers.values():
            for tool in srv.tools:
                out.append({
                    "type": "function",
                    "function": {
                        "name": f"mcp__{srv.name}__{tool['name']}",
                        "description": (tool.get("description") or "")[:900],
                        "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
                    },
                })
        return out

    def call(self, full_name: str, args: dict) -> tuple[str, bool]:
        route = self.routes.get(full_name)
        if not route:
            return f"没有这个 MCP 工具：{full_name}", True
        server, tool = route
        try:
            return self.servers[server].call(tool, args)
        except Exception as e:
            return f"{type(e).__name__}: {e}", True

    def close(self) -> None:
        for srv in self.servers.values():
            srv.close()
