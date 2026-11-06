"""FastAPI 服务端：把 agent 循环的事件流搬到网络上。

两条出口复用同一个 run_agent()，事件格式完全一致：
- GET  /stream?prompt=…   NDJSON 流，curl -N 直接看（没有前端也能验收）
- WS   /ws                WebSocket，第三步的界面就接这个
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent.config import Settings, user_env_path
from agent.loop import run_agent
from agent.skills import load_skills

ROOT = Path(__file__).parent
UI_DIR = ROOT / "ui"

app = FastAPI(title="desktop-agent", version="0.2.0")


def current_settings() -> Settings:
    return Settings.from_env()


# --------------------------------------------------------------------------- #
# 健康检查：先看灯泡有没有电
# --------------------------------------------------------------------------- #
def _skill_names(root) -> list[str]:
    """技能名单：只扫目录读 front matter，不注入正文——health 秒回。"""
    skills, _errors = load_skills(root)
    return [sk.name for sk in skills]


def _mcp_servers() -> list[str]:
    """只读 mcp.json 里的名字，不启动子进程——health 应该秒回。"""
    try:
        conf = json.loads((Path(__file__).parent / "mcp.json").read_text(encoding="utf-8"))
        return list((conf.get("servers") or {}).keys())
    except Exception:
        return []


@app.get("/health")
def health() -> JSONResponse:
    s = current_settings()
    return JSONResponse({
        "ok": True,
        "model": s.model,
        "root": str(s.root),
        "max_steps": s.max_steps,
        "token_budget": s.token_budget,
        "overall_timeout": s.overall_timeout,
        "step_timeout": s.step_timeout,
        "bash_timeout": s.bash_timeout,
        "has_key": bool(s.api_key),
        "mcp_servers": _mcp_servers(),
        "skills": _skill_names(s.root),
    })


# --------------------------------------------------------------------------- #
# 配置：界面里填 Key，写到用户目录（打包后 cwd 不可写）
# --------------------------------------------------------------------------- #
@app.post("/config")
def set_config(payload: dict) -> JSONResponse:
    key = str(payload.get("api_key") or "").strip()
    if not key:
        return JSONResponse({"ok": False, "error": "API Key 不能为空"}, status_code=400)

    path = user_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # 已有的 base_url / model 必须保留：界面弹窗里这两栏留空是常态，
    # 不能因为只改 Key 就把厂商地址冲掉（踩过：DeepSeek 地址被覆盖，401）
    old = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                old[k.strip()] = v.strip()

    base_url = str(payload.get("base_url") or "").strip() or old.get("OPENAI_BASE_URL", "")
    model = str(payload.get("model") or "").strip() or old.get("OPENAI_MODEL", "")

    lines = [f"OPENAI_API_KEY={key}"]
    if base_url:
        lines.append(f"OPENAI_BASE_URL={base_url}")
    if model:
        lines.append(f"OPENAI_MODEL={model}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)          # 能设就设，Windows 上无效也不报错
    except Exception:
        pass

    # 立刻生效，不用重启
    os.environ["OPENAI_API_KEY"] = key
    if base_url:
        os.environ["OPENAI_BASE_URL"] = base_url.rstrip("/")
    if model:
        os.environ["OPENAI_MODEL"] = model

    return JSONResponse({"ok": True, "path": str(path)})


# --------------------------------------------------------------------------- #
# 出口一：curl -N 能看的流（NDJSON，一行一个事件）
# --------------------------------------------------------------------------- #
@app.get("/stream")
def stream(prompt: str, root: str | None = None) -> StreamingResponse:
    settings = current_settings()
    if root:  # 允许换工作区，但只能往下不敢往上
        cand = (settings.root / root).resolve()
        if settings.root in cand.parents or cand == settings.root:
            settings.root = cand

    def emit():
        yield json.dumps({"type": "hello", "note": "curl -N 看到的事件流"}, ensure_ascii=False) + "\n"
        try:
            for ev in run_agent(prompt, settings):
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except Exception as e:  # 兜底：不能让流无声地断掉
            yield json.dumps({"type": "error", "message": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False) + "\n"

    return StreamingResponse(emit(), media_type="application/x-ndjson")


# --------------------------------------------------------------------------- #
# 出口二：WebSocket（界面接这个，也能中途 cancel）
# --------------------------------------------------------------------------- #
@app.websocket("/ws")
async def ws_endpoint(sock: WebSocket) -> None:
    """一条连接三个任务：recv 只管收、send 只管发、work 只管跑。

    切忌两个任务同时 await receive_text()——那会把连接卡死，
    表现是"第一次对话正常，第二次连不上"。
    """
    await sock.accept()
    settings = current_settings()
    loop = asyncio.get_running_loop()

    out: asyncio.Queue = asyncio.Queue()   # 要发给前端的事件
    jobs: asyncio.Queue = asyncio.Queue()  # 待跑的任务（一个跑完才接下一个）
    state: dict = {"history": [], "cancel": threading.Event()}

    async def recv_loop() -> None:
        try:
            while True:
                raw = await sock.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await out.put({"type": "error", "message": "消息不是合法 JSON"})
                    continue
                kind = msg.get("type")
                if kind == "cancel":
                    state["cancel"].set()
                elif kind == "ping":
                    await out.put({"type": "pong"})
                elif kind == "start":
                    prompt = (msg.get("prompt") or "").strip()
                    if not prompt:
                        await out.put({"type": "error", "message": "prompt 不能为空"})
                    else:
                        await jobs.put(prompt)
                else:
                    await out.put({"type": "error", "message": f"不认识的消息类型：{kind}"})
        except WebSocketDisconnect:
            pass  # 正常：前端关页面
        finally:
            state["cancel"].set()  # 断开即停止，别在后台空转
            jobs.put_nowait(None)

    async def send_loop() -> None:
        while True:
            ev = await out.get()
            if ev is None:
                break
            try:
                await sock.send_json(ev)
            except Exception:
                break

    async def work_loop() -> None:
        while True:
            prompt = await jobs.get()
            if prompt is None:
                break
            cancel = threading.Event()
            state["cancel"] = cancel
            finish: asyncio.Queue = asyncio.Queue()

            def runner() -> None:  # 阻塞的生成器扔进线程，别压在事件循环上
                try:
                    for ev in run_agent(prompt, settings, state["history"], cancel):
                        loop.call_soon_threadsafe(out.put_nowait, ev)
                        if ev.get("type") == "done":
                            loop.call_soon_threadsafe(finish.put_nowait, ev.get("content", ""))
                except Exception as e:
                    loop.call_soon_threadsafe(
                        out.put_nowait, {"type": "error", "message": f"{type(e).__name__}: {e}"})
                finally:
                    loop.call_soon_threadsafe(finish.put_nowait, None)

            threading.Thread(target=runner, daemon=True).start()

            answers = []
            while True:  # 等这一轮跑完（含被中断），再接下一个任务
                ans = await finish.get()
                if ans is None:
                    break
                answers.append(ans)
            if answers:  # 多轮上下文：只留提问和最终回答，中间过程不进历史
                state["history"] = state["history"] + [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": answers[-1]},
                ]

        await out.put(None)  # 收工：通知 send_loop 排空后退出

    tasks = [asyncio.create_task(t) for t in (recv_loop(), send_loop(), work_loop())]
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# --------------------------------------------------------------------------- #
# 界面：ui/index.html 存在就返回它（注意必须显式返回文件，
# 否则这个 @app.get("/") 会抢在 StaticFiles 之前，把首页变成 JSON）
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    page = UI_DIR / "index.html"
    if page.exists():
        return FileResponse(page, headers={"Cache-Control": "no-store"})
    return JSONResponse({
        "ok": True,
        "msg": "服务端已就绪，界面还没做（第三步）",
        "endpoints": {"/health": "健康检查", "/stream?prompt=…": "curl -N 看事件流", "/ws": "WebSocket"},
    })


if UI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
