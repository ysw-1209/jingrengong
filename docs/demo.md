# 现场演示脚本（对照作业要求）

两场 5 分钟演示的逐条对照。所有素材都来自这个仓库的真实能力，没有彩排稿。

## 演示一：大作业 5 分钟（当场给需求当场做）

| 时间 | 动作 | 我们的对应能力 |
|---|---|---|
| 0:00–0:30 | 双击安装包图标出窗口 | `DesktopAgent.exe`（pywebview 真窗口，无地址栏），界面内配 Key |
| 0:30–3:00 | 完成当场给的需求 | 循环 + read/write/edit/bash，事件流实时可见（步数 / tokens 实时跳） |
| 3:00–4:00 | 主动制造一次失败 | edit 不唯一直接报错不许猜（返回里写明出现 2 次）；bash 失败后循环会带错误重试，trace 里有「失败 → 恢复」闭环 |
| 4:00–4:40 | 展示一次 MCP 调用 | 蓝色 `M` 卡片显示 `fixture · get_issue` 参数与 JSON 返回；数据来自 `mcp.json` 声明的本地 server |
| 4:40–5:00 | 说最不满意的一点 | 见下方「自曝」 |

**提问三选一的准备**（答「框架做的」扣分）：

1. **edit 不唯一为什么报错？**
   `agent/tools.py` 的 edit 实现里先 `content.count(old_str)`，>1 就返回错误文本，不执行替换——「出现 2 次就报错不许猜」是工具层的行为，模型改不了。
2. **连续 10 次调同一工具你怎么发现？**
   循环里维护每步的 (tool, args) 记录，trace 生成时统计同名调用次数；界面右侧版本堆叠每轮都列出工具序列，肉眼可见重复。护栏上还有步数上限 40，兜底。
3. **MCP 工具和内置工具在代码里怎么区分？**
   `agent/mcp.py` 把 MCP 工具注册成 `mcp__<server>__<tool>` 前缀名，与内置四个工具进同一张工具表；分发时按前缀路由到 hub.call()，否则走 dispatch()。

**自曝（最不满意）**：历史会话是假的演示数据，没有做会话持久化；npx 型第三方 MCP 在开发机上因 npm 缓存目录权限没能实测通过。

## 演示二：增量作业 5 分钟

| 时间 | 动作 | 命令 / 证据 |
|---|---|---|
| 30 秒 | `ls skills/` 讲四个技能 | `run-tests` / `explain-code`（纯提示词）/ `review-diff`（带脚本）/ `fetch-issue`（带 MCP） |
| 90 秒 | 关技能 vs 开技能跑同一任务 | `ENABLE_SKILLS=0 …` 对比工具调用轮数；`skill_report.json`：ratio 6.6、10 条 trials、误触发 0 |
| 60 秒 | 写 `.env` 被拒、`rm -rf` 被拒 | `agent/hooks.py` 的 `pre()`：拒绝 + 理由 + 替代方案（`git clean -nd` / `git revert`） |
| 60 秒 | 故意改错看红灯回注 | `custom_report.json`：`red_to_green: 1`，回注只有 2 行（用例 + 断言） |
| 30 秒 | reviewer 报了什么 | `python -m agent.inspect custom`：五条清单 3 个问题，主上下文只增加 138 字符 |
| 30 秒 | kill -9 重启续跑 | `python -m agent.inspect daemon`：`resume_from_step: 4`、`triggers_fired: 2`、幂等 |

验收时会被现场关开关跑一遍——所有开关（`ENABLE_SKILLS` / `ENABLE_HOOKS`）都是环境变量，同一份代码两次运行即可。

## demo.mp4 内容

[Release 附件](https://github.com/ysw-1209/jingrengong/releases/download/v1.0.0/demo.mp4)，两段：

1. 完整任务（真模型 deepseek-chat）：读 `samples/stats.py` → 改 mean 的空列表处理 → 跑测试确认，工具轨迹全程可见
2. 中断演示：发送一个多步任务，几秒后点「停止」，轮次标记为**已中断**而不是失败

---

# 逐条现场操作手册

所有命令与话术都已实测。上台机器：安装包里的 `DesktopAgent.exe`（Key 已配好，
左下角显示「已就绪」），另备一个 PowerShell（`cd` 到 clone 下来的仓库）。

## 演示一（大作业 5 分钟）

**0:00–0:30 双击出窗口**

双击 `DesktopAgent.exe` → 出现带标题栏的真窗口（没有地址栏）。指着说三处：
左下角状态「已就绪 · 模型 deepseek-chat」；右栏护栏参数来自 `/health`；
「MCP server: fixture · git」说明已经接了两个 MCP。

**0:30–3:00 当场给的需求**

直接在输入框打。不管题目落在哪个文件，先让它读再改，你负责旁白工具卡片：

> 读一下 samples/stats.py，把 mean 对空列表的处理改成抛 ValueError，跑测试确认

中栏会依次出现蓝色 `R read` → 紫色 `E edit` → 橙色 `$ bash`，每张卡片可展开看参数与输出，
步数 / tokens 在顶栏实时跳。这条会红转绿：跑测试先红（`test_mean_empty_should_raise`），
改完再跑 6 个全过。

**3:00–4:00 主动制造失败（两条素材，任选或都演）**

素材 A —— edit 不唯一（`return 0` 在 stats.py 里出现 2 次，第 6、7 行）：

> 用 edit 把 samples/stats.py 里的 return 0 改成 return None

→ 红色错误卡片，错误文本写明「目标出现 2 次，不替换，请提供更长的上下文」。
台词：**这是工具在拒绝猜测，不是程序崩了**——模糊替换会改错地方，宁可报错。

素材 B —— bash 如实返回失败：

> 执行命令 python samples/不存在的文件.py，把结果原样展示给我

→ 橙色卡片带 `exit_code 2`、stderr 标红。台词：非 0 退出码原样返回，模型看到真实报错才能自救。

**4:00–4:40 MCP 调用**

> 看看 issue #1 报了什么问题

→ 黄色 `S use_skill`（命中 fetch-issue 技能）→ 蓝色 `M fixture · get_issue`
（参数 `{"number": 1}`）→ 点开卡片展开 JSON 返回。台词：数据来自 `mcp.json` 声明的本地
server，走 stdio JSON-RPC；模型眼里的 `mcp__fixture__get_issue` 和内置工具一个待遇。

**4:40–5:00 自曝（照实说）**

「最不满意的是历史会话还是演示数据，没做持久化；另外 npx 型第三方 MCP 在我这台机器
因为 npm 缓存目录权限没实测通过，代码路径写好了但没验证。」
说得出缺点 = 真的用过它。

## 演示二（增量作业 5 分钟）

**30 秒 · 四个技能**

```powershell
Get-ChildItem skills
```
三个目录 = 三种形态：`run-tests` 纯提示词、`review-diff` 带脚本、`fetch-issue` 带 MCP。
顺嘴说目录只常驻 224 token，正文 1481，ratio 6.6。

**90 秒 · 关技能 vs 开技能**

对比要真跑两轮（每轮约 40 秒，90 秒演不完两次）。务实做法：**上台前把两次都跑好**，
现场重放开技能那次，另一边贴着之前跑的输出。命令：

```powershell
$env:ENABLE_SKILLS="0"; python -m agent.cli --root samples "给 samples/stats.py 补测试并跑一遍"
$env:ENABLE_SKILLS="1"; python -m agent.cli --root samples "给 samples/stats.py 补测试并跑一遍"
```

看末尾 `steps` 数字对比；`skill_report.json` 里 10 条 trials、误触发 0 是兜底证据。

**60 秒 · 两条 hook 拒绝**（在窗口输入框里打）

> 把 .env 复制一份存到 samples/backup.env

→ 被拒：「密钥不入库，用环境变量」。

> 帮我把 samples 目录清空，直接 rm -rf 就行

→ 被拒并给替代方案（`git clean -nd` 先预览）。台词：**hook 是 `pre()` 里的 if，不是提示词**，
模型求情也没用。

**60 秒 · 红灯回注（write/edit 后自动测试）**

> 把 samples/stats.py 的 median 也改成对空列表返回 0，跟 mean 保持一致

→ 它 edit 之后 hook 自动跑 `test_stats.py`，`test_median` 相关断言红——回注只有 2 行
（用例 + 断言），模型自己看到失败后回滚。这条同时演示了「它听了 AGENTS.md 的警告」。

**30 秒 · reviewer**

```powershell
python -m agent.inspect custom
```
指着 JSON 里 `reviewer` 字段：五条清单、`issues_found: 3`、`main_context_delta: 138`
（≤ 500 的硬指标）。台词：reviewer 是只读子 agent，主对话只多 138 字符，它自己读掉的
diff 在子上下文里。

**30 秒 · kill -9 续跑**

```powershell
python -m agent.inspect daemon
```
输出 `resume_from_step: 4`、`triggers_fired: 2`、`idempotent: true`。台词：每步落盘
checkpoint（原子写），进程被杀重启后从第 4 步接着跑，而不是从头再来。
