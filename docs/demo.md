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
| 30 秒 | `ls skills/` 讲三个技能 | `run-tests`（纯提示词）/ `review-diff`（带脚本）/ `fetch-issue`（带 MCP） |
| 90 秒 | 关技能 vs 开技能跑同一任务 | `ENABLE_SKILLS=0 …` 对比工具调用轮数；`skill_report.json`：ratio 6.4、8 条 trials、误触发 0 |
| 60 秒 | 写 `.env` 被拒、`rm -rf` 被拒 | `agent/hooks.py` 的 `pre()`：拒绝 + 理由 + 替代方案（`git clean -nd` / `git revert`） |
| 60 秒 | 故意改错看红灯回注 | `custom_report.json`：`red_to_green: 1`，回注只有 2 行（用例 + 断言） |
| 30 秒 | reviewer 报了什么 | `python -m agent.inspect custom`：五条清单 3 个问题，主上下文只增加 138 字符 |
| 30 秒 | kill -9 重启续跑 | `python -m agent.inspect daemon`：`resume_from_step: 4`、`triggers_fired: 2`、幂等 |

验收时会被现场关开关跑一遍——所有开关（`ENABLE_SKILLS` / `ENABLE_HOOKS`）都是环境变量，同一份代码两次运行即可。

## demo.mp4 内容

[Release 附件](https://github.com/ysw-1209/jingrengong/releases/download/v1.0.0/demo.mp4)，两段：

1. 完整任务（真模型 deepseek-chat）：读 `samples/stats.py` → 改 mean 的空列表处理 → 跑测试确认，工具轨迹全程可见
2. 中断演示：发送一个多步任务，几秒后点「停止」，轮次标记为**已中断**而不是失败
