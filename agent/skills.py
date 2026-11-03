"""技能系统：目录常驻、正文按需。

这是增量 A 的命门——启动时只把 name + description 拼成一张目录表，
模型判断要用时再调 use_skill(name)，正文这时才进上下文。

20 个技能全塞进去是 2 万 token，每轮都付；只放目录是 800。
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
TRIGGER = re.compile(r'["“]([^"”\n]{2,16})["”]')


USE_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "use_skill",
        "description": (
            "加载一个技能的正文（步骤、禁令、完成判据）。\n"
            "目录里只给了技能名和用途，正文没有加载；判断任务命中某个技能时，"
            "先调用它取回步骤，再照着做。\n"
            "什么时候别用：任务与目录里的技能都不相关时，直接干活，不要为了用而用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名，必须与目录里列出的完全一致"},
            },
            "required": ["name"],
        },
    },
}


def resource_root() -> Path:
    """打包后资源在 sys._MEIPASS；源码运行时是项目根目录。"""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def skills_dir(root: Path | None = None) -> Path:
    """技能目录：优先环境变量，其次工作区，最后项目自带。"""
    env = os.environ.get("AGENT_SKILLS_DIR")
    if env:
        return Path(env).resolve()
    if root is not None and (root / "skills").is_dir():
        return (root / "skills").resolve()
    return resource_root() / "skills"


def estimate_tokens(text: str) -> int:
    """粗估：中文约 1 字 1 token，英文约 4 字符 1 token，混排取 //2。"""
    return max(1, len(text) // 2)


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    has_scripts: bool = False
    uses_mcp: bool = False
    triggers: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return f"---\nname: {self.name}\ndescription: {self.description}\n---\n\n{self.body}"

    def catalog_line(self) -> str:
        desc = self.description.strip().replace("\n", " ")
        if len(desc) > 160:
            desc = desc[:160] + "…"
        return f"- {self.name}: {desc}"


def _parse(text: str) -> tuple[dict, str]:
    m = FRONT_MATTER.match(text)
    if not m:
        return {}, text
    head, body = m.group(1), text[m.end():]
    meta: dict = {}
    for line in head.splitlines():
        if ":" not in line or line.strip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, body


def load_skills(root: Path | None = None) -> tuple[list[Skill], list[str]]:
    """扫描 skills/*/SKILL.md。返回 (技能列表, 加载失败说明)。

    只读 front matter 与正文（正文留在内存里但不进 system prompt），
    排序按 name 固定——缓存友好的硬要求。
    """
    base = skills_dir(root)
    skills: list[Skill] = []
    errors: list[str] = []
    if not base.is_dir():
        return skills, [f"技能目录不存在：{base}"]

    for sub in sorted(base.iterdir(), key=lambda p: p.name):
        if not sub.is_dir():
            continue
        skill_md = sub / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
        except Exception as e:  # 编码坏了也要说清楚，不要静默跳过
            errors.append(f"{sub.name}/SKILL.md 读不了：{e}")
            continue
        meta, body = _parse(text)
        name = (meta.get("name") or sub.name).strip()
        desc = (meta.get("description") or "").strip()
        if not desc:
            errors.append(f"{sub.name}/SKILL.md 缺少 description，模型永远认不出来")
            continue
        skills.append(Skill(
            name=name,
            description=desc,
            body=body.strip(),
            path=skill_md,
            has_scripts=(sub / "scripts").is_dir(),
            uses_mcp=("mcp__" in body) or ("MCP" in body),
            triggers=[t for t in TRIGGER.findall(desc) if t.strip()],
        ))

    skills.sort(key=lambda s: s.name)
    return skills, errors


def catalog_text(skills: list[Skill], enabled: bool = True) -> str:
    """常驻上下文的那张目录表。关掉技能系统时返回空串。"""
    if not enabled or not skills:
        return ""
    lines = [
        "## 可用技能（skills/）",
        "命中时先调 use_skill(name) 取步骤；目录里没有合适的就直接干活。",
        "",
    ]
    lines.extend(s.catalog_line() for s in skills)
    return "\n".join(lines)


def render_skill(name: str, skills: list[Skill]) -> str:
    """use_skill 的返回值：命中才注入正文。"""
    for s in skills:
        if s.name == name:
            return (
                f"# 技能：{s.name}\n\n"
                f"{s.body}\n\n"
                f"（技能文件：{s.path.name}；脚本目录："
                f"{'有' if s.has_scripts else '无'}）"
            )
    known = "、".join(s.name for s in skills) or "（无）"
    return f"没有这个技能：{name}。可用技能：{known}。请只用目录里列出的技能名。"


def match_skill(query: str, skills: list[Skill]) -> tuple[str | None, int, dict[str, int]]:
    """离线触发测试用：按 description 里的触发语打分，不调用模型。

    只在触发语命中时给 3 分，避免"测试"这种泛词把闲聊也点燃。
    """
    scores: dict[str, int] = {}
    for s in skills:
        score = 0
        for t in s.triggers:
            if t and t in query:
                score += 3
        # 技能名拆词（run-tests → run / tests）只给 1 分，兜底不主导
        for part in re.split(r"[-_]", s.name):
            if len(part) >= 3 and part.lower() in query.lower():
                score += 1
        scores[s.name] = score

    best = max(scores, key=lambda k: scores[k]) if scores else None
    best_score = scores.get(best, 0) if best else 0
    # 阈值 2：只看 name 拆词（1 分）不足以触发，必须命中触发语（3 分）
    return (best if best_score >= 2 else None), best_score, scores
