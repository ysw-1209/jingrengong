"""桌面入口：pywebview 起一个真窗口，里面跑本地 FastAPI。

双击出窗口、有标题栏、不是浏览器里的 localhost 页面。
uvicorn 放后台线程，webview 必须占主线程（macOS/Windows 都一样）。
"""
from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn

from server import app


def bundle_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def prepare_workspace() -> Path:
    """打包后 cwd 可能不可写：默认工作区放到用户目录，首次启动带好样例。"""
    ws = Path(os.environ.get("AGENT_ROOT") or (Path.home() / "DesktopAgentWorkspace"))
    ws.mkdir(parents=True, exist_ok=True)
    src = bundle_root()
    if not (ws / "samples").exists() and (src / "samples").is_dir():
        shutil.copytree(src / "samples", ws / "samples")
    if not (ws / "AGENTS.md").exists() and (src / "AGENTS.md").is_file():
        shutil.copy(src / "AGENTS.md", ws / "AGENTS.md")
    os.environ.setdefault("AGENT_ROOT", str(ws))
    return ws


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(url: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def main() -> int:
    import webview  # 延迟导入：CLI / selftest 不需要它

    prepare_workspace()
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", access_log=False))

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    if not wait_ready(base + "/health"):
        print("服务端没起来，窗口没开。", file=sys.stderr)
        return 1

    webview.create_window(
        "Desktop Agent · 本机编程助手",
        base + "/",
        width=1280,
        height=840,
        min_size=(980, 640),
        text_select=True,
    )
    webview.start()          # 阻塞到窗口关闭

    server.should_exit = True
    thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
