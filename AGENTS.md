# 项目地图

- `agent/`：循环、四个工具、技能加载、hooks、MCP 客户端。改工具先看 `tools.py` 顶部的 SCHEMAS。
- `agent/loop.py`：唯一的主循环，产出事件流；CLI / server / 界面都只是消费者。
- `server.py`：FastAPI，`/health`、`/stream`（NDJSON）、`/ws`（界面接这个）。
- `ui/`：三栏界面，原生 HTML/CSS/JS，没有构建步骤。
- `skills/`：一个技能一个文件夹，`SKILL.md` 头部是索引、正文是流程。
- `samples/`：演示用样例。`stats.py` 里的空列表 bug 是故意的，别顺手修掉。

# 构建与测试

```bash
python -m unittest discover -s samples -p "test_*.py" -q   # 预期 1 红（故意留的 bug）
python -m agent.selftest                                    # 冒烟，10 秒内
python -m agent.inspect skills --out skill_report.json      # 技能验收数字
python -m agent.inspect custom --out custom_report.json     # 定制层验收数字
```

# 代码风格

- 只依赖标准库 + `requests` + `fastapi`，不新增第三方依赖。
- 工具错误一律抛 `ToolError`，错误信息要写「为什么 + 下一步怎么办」。
- 禁止 `except: pass`；兜底捕获必须把异常变成人能读懂的一句话。
- 注释写「为什么这样做」，不写「这一行在做什么」。

# 坑

- 这是 Python 3.13，`X | None` 可以直接写，不要用 `Optional`。
- 所有文件路径必须过 `tools.resolve_in_root()`，不要直接 `open()`。
- `requests` 流式响应默认按 ISO-8859-1 解码，中文会乱码，必须显式 `resp.encoding = "utf-8"`。
- WebSocket 一条连接只能有一个 `receive_text()`，两个任务同时收会卡死第二次握手。
- 打包后 `ui/` 在 `sys._MEIPASS`，可写目录用 `Path.home()`，不要写死相对路径。
- 技能目录排序必须稳定（按 name），否则缓存前缀每次都变。
