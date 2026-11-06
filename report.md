# 增量作业报告

> 三个数字文件都在仓库根：`skill_report.json`、`custom_report.json`、`daemon_report.json`。
> 下面的数字全部是这三份 JSON 里的字段，没有手写估算。

## 1. 每个增量做完之后，哪件事变快了

### 增量 A｜技能：常驻上下文从 1481 降到 224

`skill_report.json`：`catalog_tokens: 224`、`full_tokens: 1481`、`disclosure_ratio: 6.6`。

启动时只有 224 token 的目录进 system prompt；四个技能正文加起来 1481 token，
只有在命中并调用 `use_skill()` 之后才注入。也就是说**平时每轮少付 1257 token**，
四个技能全用一遍才回到 1481。技能越多，这个差距越大——这正是"800 还是 20000"那一页的机制。

配套的可验证指标：10 条真话（8 条该命中 + 2 条闲聊）全部判对，`false_positive: 0`。
触发靠 description 里的引号短语（"跑一下测试"、"审一遍"、"看看这个 issue"、"讲讲这段代码"），
不是靠技能名猜，所以"今天天气怎么样"不会点燃 `run-tests`。
第四个技能 `explain-code` 是后来加的：加载器按 `skills/` 目录自动发现，
**加技能没有改一行加载代码**——目录扫描本身就是扩展点。

### 增量 B｜定制层：reviewer 让主上下文只增加 138

`custom_report.json` 里 `reviewer.main_context_delta: 138`，
而它自己读掉的 diff 是 `subagent_tokens: 264`。主 agent 只看到"问题列表"，
看不到整份 diff——这就是子 agent 唯一的价值，也是这题唯一量得出来的东西。

自动测试那条：`autotest.red_to_green: 1`，`injected_lines: 2`。
改错 → 测试红 → 只回注 2 行（用例 + 断言）→ 改对 → 绿，上下文没有被 traceback 淹掉。

### 增量 C｜常驻：从第 4 步继续，而不是从第 1 步重来

`daemon_report.json`：`resume_from_step: 4`、`triggers_fired: 2`、
`notify_fired: true`、`idempotent: true`。

前 3 步落盘后模拟 kill -9，重启从第 4 步接上；定时任务第一次触发 2 条，
第二次触发 0 条（`last_run` 已记，不会重复执行）。

## 2. 一次判断错误

**原以为**：把技能目录拼进 system prompt 就行，先跑通再说。
**跑完发现**：`disclosure_ratio` 只有 4.0，离及格线 5 还差一截——因为目录说明写了 4 行、
每个技能正文又偏薄，两头一起把比例压低了。

**改成了什么**：
1. 目录说明从 4 行压到 1 行（只留"命中时先调 use_skill"）；
2. description 在目录里截断到 160 字；
3. 正文补充真正有用的东西（输出模板、五条清单的判据表、MCP 字段白名单），
   正文变厚的同时目录不变——比例回到 6 以上（加第四个技能后为 6.6）。

这不是"删正文凑比例"，是"目录只做索引，细节留在正文"。

**第二个错误（更隐蔽）**：reviewer 第一次只报了 1 条问题，我以为是正则写错了。
实际是两个坑叠在一起：`git add -A` 之后工作区 `git diff` 是空的，必须看 `--cached`；
`except Exception:` 和 `pass` 在 diff 里是两行，单行正则永远匹配不到。
改成跨行正则 + 工作区与暂存区合并后，`issues_found` 从 1 变成 3。

## 3. 如果只保留一个增量，留哪个

**留技能（增量 A）。**

理由不是它分最高，是**它是唯一能带走的**：`skills/` 目录换个仓库复制过去就能用，
`SKILL.md` 不写死路径、不依赖本项目；而 hooks 里的 `rm -rf`、`.env` 规则是
绑在这个仓库上的约束，reviewer 的五条清单也贴着 Python 项目。
常驻（C）机制最漂亮，但要求把循环改造成可重入状态机，成本比 A+B 加起来还高，
我这次只做了 checkpoint + routines 的最小验证，没有把真模型接进去。

一句话：技能是资产，hook 是护栏，常驻是基建。只留一个，留能复利的那个。
