# desktop-agent（后端 + 服务端 + 三栏界面）

一个能脱离界面单独跑的智能体：模型调用循环 + 四个工具 + FastAPI/WebSocket 服务端 + 三栏工作台。

## 三步启动

```bash
cp .env.example .env          # 1. 填 OPENAI_API_KEY（换国产模型就改 BASE_URL / MODEL）
pip install -e .               # 2. 装依赖（全部版本钉死：requests / fastapi / uvicorn / websockets）
python -m agent.cli --root samples "读一下 stats.py，把 mean 对空列表的处理改掉，然后跑测试"
```

## 第 2 步：起服务

```bash
python -m uvicorn server:app --port 8000 --reload
curl http://127.0.0.1:8000/health          # 先看灯泡有没有电
curl -N "http://127.0.0.1:8000/stream?prompt=看一下工作区"
```

关掉前端也能看到同一条事件流：

```jsonc
{"type": "start", "model": "fake-model", "root": "…/samples"}
{"type": "content_delta", "text": "先看一下目录："}
{"type": "tool_call", "name": "bash", "args": {"command": "echo hi-agent"}}
{"type": "tool_result", "name": "bash", "text": "$ echo hi-agent\n[exit_code] 0\n[stdout]\nhi-agent", "is_error": false}
{"type": "done", "content": "跑完了，一切正常。", "steps": 2}
```

| 出口 | 用途 |
|---|---|
| `GET /health` | 健康检查：model / root / 护栏参数 / 有没有 Key |
| `GET /stream?prompt=…` | NDJSON 流，`curl -N` 直接看（没有前端也能验收） |
| `WS /ws` | WebSocket，第三步的界面接这个；`{"type":"start","prompt":…}`、`{"type":"cancel"}`、`{"type":"ping"}` |

WebSocket 会话结构：**一条连接三个任务，recv 只收、send 只发、work 只跑**。
两个任务同时 `await receive_text()` 会把连接卡死——表现是"第一次对话正常，第二次连不上"，已踩过。

CLI 不给 prompt 就是多轮对话；`--trace docs/trace.md` 会把一次完整运行（含失败与恢复）写成 markdown。

## 第 3 步：三栏界面

起服务后直接打开 <http://127.0.0.1:8000/>。界面在 `ui/` 下，原生 HTML/CSS/JS，没有构建步骤。

| 栏位 | 内容 |
|---|---|
| 左 | 历史会话（演示数据，点击只换标题和占位说明）+ 连接状态，断开可点击重连 |
| 中 | 对话气泡 + 工具轨迹卡片（参数、退出码、输出可展开）+ 输入框 / 停止 |
| 右 | 版本堆叠（每轮 `done` 自动压一张卡）+ 护栏参数（来自 `/health`） |

- 「停止」发 `{"type":"cancel"}`，被中断的轮次标成**已中断**，不显示成失败
- 工具卡片按 `read / write / edit / bash` 着色，`is_error` 标红，退出码贴在卡片右上角
- 快捷键：`Enter` 发送、`Shift+Enter` 换行、`Esc` 停止
- 窄屏（< 980px）先收右栏，< 720px 收左栏

界面的历史会话是假的，但**工具轨迹、版本入栈、护栏参数都来自真实运行**——演示时别把这两件事混着讲。

## 运行记录（trace）

`docs/trace.md` 是一次完整运行记录，重点是**失败与恢复**：`bash` 跑测试 `exit 1` → `read` 看源码 → `edit` 精确改一行 → 再跑 `exit 0`。

```bash
python -m agent.cli --root samples --trace docs/trace.md "测试有个失败，找出原因修掉"
```

trace 会自动统计步数、耗时、工具调用次数，并识别「同一工具先失败、后来重试成功」的闭环。
仓库里那份是离线脚本化录制的样本（工具真执行，模型是预设剧本）；配好 `.env` 跑上面这条命令会覆盖成真模型记录。

## 增量能力（v1.1 – v1.3）

### 技能：目录常驻，正文按需

`skills/` 下三个技能，三种形态：

| 技能 | 形态 | 验证什么 |
|---|---|---|
| `run-tests` | 纯提示词 | 只靠流程描述就能生效 |
| `review-diff` | 提示词 + `scripts/collect_diff.py` | 脚本能被 bash 调起来 |
| `fetch-issue` | 提示词 + MCP 工具 | 技能能编排已接好的 MCP |

启动时 system prompt 里只有 178 token 的目录（name + description），正文要等模型调
`use_skill(name)` 才注入。三个技能正文合计 1134 token，`disclosure_ratio = 6.4`。

关掉技能系统：`ENABLE_SKILLS=0 python -m agent.cli ...`，同一份代码、两次运行，可现场对比。

### 定制：AGENTS.md + 三条 hook + reviewer

- `AGENTS.md` 从工作区往上找（项目级），再读 `~/.desktop-agent/AGENTS.md`（全局），按「全局 → 项目」拼进 system prompt
- 三条 hook 都在 `agent/hooks.py` 的代码里，不是提示词：
  - 写 `.env` / `*.pem` / `*.key` → 拒绝，理由「密钥不入库」
  - `rm -rf` / `git push --force` / `git reset --hard` → 拒绝 + 替代方案
  - `write` / `edit` 之后自动跑测试，只回注「用例 + 断言」两行
- `agent/reviewer.py` 是只读子 agent：只看 diff、五条清单、只回一条消息（主上下文只增加 138 字符）

关掉 hook：`ENABLE_HOOKS=0`。

### 常驻（选做）

`agent/daemon.py`：每一步落盘 checkpoint，重启从断点继续；`routines.yaml` 到点触发，记 `last_run` 防止重复执行。
演示：`python -m agent.inspect daemon`，会输出 `resume_from_step: 4`。

## 连接 MCP

`mcp.json` 声明 server，`agent/mcp.py`（stdio / JSON-RPC）逐个拉起，工具聚合成
`mcp__<server>__<tool>` 注入智能体工具表——模型直接调用，和内置四个工具一个待遇。

```jsonc
{
  "servers": {
    "git": { "command": "python", "args": ["scripts/mcp_git.py"] },
    "fs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
      "env": { "npm_config_cache": ".agent/npm-cache" }
    }
  }
}
```

- **已验证**：两个本地 server 同时在线——`fixture`（issue 数据源）+ `git`（只读
  status/diff/log），共 4 个工具全部真实调通；`python -m agent.selftest` 会检查
  MCP 工具能否列出
- **`env` 字段**：第三方 server 要的 token / 缓存目录写在这里，不进代码
- **npx 型 server**：已实现 `.CMD` 启动器包装（`cmd /c`）和 60 秒握手；
  本机 npm 缓存目录权限受限（EPERM），官方 filesystem 包没能拉起来——
  代码路径是对的，换台能访问 npm 的机器即可
- **单个 server 起不来不阻塞**：记进 `hub.errors`，智能体照常跑其余工具
- `/health` 会返回 `mcp_servers` 名单，界面右栏「MCP server」一行可见；
  工具轨迹里显示成 `git · git_status`（图标 M、蓝色），和内置工具区分

## 验收命令（数字全部由程序打印）

```bash
python -m agent.selftest                              # 5 项冒烟，10 秒
python -m agent.inspect skills --out skill_report.json
python -m agent.inspect custom --out custom_report.json
python -m agent.inspect daemon --out daemon_report.json
```

## 桌面窗口与安装包

```bash
pip install -e ".[desktop,build]"
python desktop.py                                      # 开发时直接出窗口
python -m PyInstaller --noconfirm --clean desktop-agent.spec
```

产物 `dist/DesktopAgent/DesktopAgent.exe`（onedir，无控制台），压缩后约 22 MB。
打包细节与三个坑见 [docs/build.md](docs/build.md)；`console=False`，双击出的是窗口不是浏览器。

## 上传 GitHub 与发 Release

仓库已 `git init`，`.gitignore` 已排除 `.env`、`dist/`、`.agent/`、`*.log`。
我没有你的账号凭据，推送这一步需要你自己执行：

```bash
# 先在 GitHub 网页新建 public 仓库 desktop-agent（不要勾选 README）
git remote add origin https://github.com/<你的账号>/desktop-agent.git
git branch -M main
git add -A
git commit -m "feat: 技能系统 + 定制层 + 桌面窗口"
git tag v1.0.0
git push -u origin main --tags
```

然后在 Releases → Draft a new release，tag 选 `v1.0.0`，
附件上传 `dist/DesktopAgent-windows-x64.zip`。

## 目录

```
agent/
├── config.py   配置与三道护栏（步数 / 预算 / 超时）
├── tools.py    四个工具的说明书 + 实现
├── llm.py      OpenAI 兼容流式调用（换厂商只改 .env）
├── loop.py     主循环：决策 → 动作 → 观察 → 再决策（生成器，产出事件流）
└── cli.py      终端入口
server.py        FastAPI：/health、/stream（NDJSON）、/ws（WebSocket）
ui/              三栏界面：index.html + style.css + app.js（无构建）
samples/         用来试手的样例代码（stats.py 里有个 bug）
```

## 事件流（后面 WebSocket 直接转发这些事件）

| type | 含义 |
|---|---|
| `start` | 启动，带 model / root |
| `content_delta` | 模型输出的增量文字 |
| `tool_call` | 调了哪个工具、参数是什么 |
| `tool_result` | 工具返回（`is_error` 标红用） |
| `done` / `error` / `cancelled` | 正常结束 / 出错 / 被中断 |

## 四个工具的语义（课件第 36 页）

| 工具 | 要点 | 最容易做错的地方 |
|---|---|---|
| read | 文本按行带行号；图片走 base64 多模态块；大文件分页并提示"还有 N 行" | 图片 base64 塞进 text 字段 |
| write | 自动建父目录；临时文件 + rename 原子写；覆盖时报前后行数 | 忘建目录 / 写一半崩了 |
| edit | 精确替换 + 唯一性检查，出现 2 次就报错不许猜，返回 unified diff | 做成模糊匹配或整文件重写 |
| bash | stdout + stderr + exit_code，非 0 也正常返回；**必须有超时和截断** | 无超时 → pip install 卡死 |
