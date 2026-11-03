---
name: review-diff
description: 审查当前未提交的改动，只报告问题不改代码。Use this when 用户说"审一下"、"审一遍"、"review 一下"、"看看 diff"、"这段 diff 有没有问题"。
---

## 什么时候用
改完代码、提交之前，想让人（或它自己）过一遍 diff。

## 步骤
1. 先跑 `python skills/review-diff/scripts/collect_diff.py`，拿到变更清单和逐文件 diff。
   脚本会截断超长 diff，不要重复读整个仓库。
2. 只读 diff 和必要的上下文，不要读没改过的文件。
3. 按五条清单逐条检查：
   - 有没有改到不该改的文件（配置、生成物、别人的模块）
   - 有没有新增依赖（`pyproject.toml` / `requirements` 的改动）
   - 有没有 `except: pass` 或吞异常
   - 新分支有没有测试覆盖
   - 有没有调试残留（`print(`、`breakpoint()`、`TODO`、`FIXME`）
4. 每条给「文件:行 + 问题 + 建议」；没有问题就明确说没有。

## 不要做什么
- 不动代码、不提交、不 stage
- 不评论纯格式偏好（缩进、引号）
- 不把 `git diff` 全文复述一遍

## 清单判据（怎么算命中）
| 项 | 判据 |
|---|---|
| 改到不该改的文件 | diff 里出现 `.env`、`dist/`、`*.lock`、别人的模块 |
| 新增依赖 | `pyproject.toml` 的 dependencies 出现 `+` 行 |
| `except: pass` | 新增行里有 `except ...: pass` 或 `except: ...` |
| 新分支没测试 | 新增 if/elif，但 diff 里没有 `test_` 文件的改动 |
| 调试残留 | 新增行含 `print(`、`breakpoint(`、`TODO`、`FIXME` |

## 怎么算做完了
- 五条清单都有结论（有问题 / 没有）
- 每个问题都能定位到文件与行号
- 结论控制在 8 行以内，不把 diff 复述一遍
