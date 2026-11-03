"""四个工具：read / write / edit / bash

规格（课件第 36 页）：
- read  文本按行带行号，图片走多模态块，大文件分页并告诉模型还剩几行
- write 自动建父目录，临时文件 + rename 原子写，覆盖时报告前后行数
- edit  精确字符串替换 + 唯一性检查，出现多次就报错，不许猜
- bash  stdout + stderr + exit_code，非 0 也正常返回，必须有超时和截断
"""
from __future__ import annotations

import base64
import difflib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings

IMAGE_SUFFIX = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}


class ToolError(Exception):
    """错误信息就是模型下一步的线索，所以要写清"为什么 + 怎么办"。"""


@dataclass
class ToolResult:
    text: str
    images: list[str] = field(default_factory=list)  # data URL，交给多模态内容块
    is_error: bool = False


# --------------------------------------------------------------------------- #
# 工具说明书：模型只凭这段话决定调不调，看不到实现
# --------------------------------------------------------------------------- #
SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": (
                "读取工作区里的文件内容（只读）。文本按行返回并带行号，图片返回可查看的图片内容。\n"
                "什么时候用：要理解一段代码、确认某行原文、查看报错上下文。\n"
                "什么时候别用：不要用它列目录或搜索（改用 bash 的 ls / rg）；"
                "不要为了改文件而把整个文件读进来再重写（改用 edit）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区根目录的路径，如 src/app.py"},
                    "offset": {"type": "integer", "description": "起始行号，从 1 开始，默认 1"},
                    "limit": {"type": "integer", "description": "最多读多少行，默认 400"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": (
                "整体写入文件：新建文件，或完整覆盖已有文件。会自动创建缺失的父目录。\n"
                "什么时候用：新建文件；或者改动极大、用 edit 反而更啰嗦时。\n"
                "什么时候别用：只改一两处时用 edit——整文件重写会丢掉你没看到的内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区根目录的路径"},
                    "content": {"type": "string", "description": "文件完整内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": (
                "对已有文件做精确字符串替换（精确匹配，不是模糊匹配，不是整文件重写）。\n"
                "什么时候用：改几行、修一个 bug、加一个函数。\n"
                "什么时候别用：新建文件用 write；不确定原文时先 read 再 edit。\n"
                "硬性要求：old 必须在文件中恰好出现 1 次；出现 0 次会告诉你没找到，"
                "出现 2 次及以上会报错并列出所在行号——这时请补充上下文让 old 变唯一，不许猜。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区根目录的路径"},
                    "old": {"type": "string", "description": "待替换的原文，必须与文件中完全一致（含缩进与空格）"},
                    "new": {"type": "string", "description": "替换后的新内容"},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "在工作区根目录执行一条 shell 命令，返回 stdout、stderr 和 exit_code。"
                "非 0 退出码也会正常返回，不会中断对话。\n"
                "什么时候用：搜索（rg）、跑测试（pytest）、git、装依赖、查看目录结构、运行脚本。\n"
                "什么时候别用：读文件内容用 read；改文件用 edit——别用 sed/echo 去改文件。\n"
                "必须给 timeout，默认 30 秒；耗时长的命令请显式加大 timeout。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令，如 pytest -q"},
                    "timeout": {"type": "number", "description": "超时秒数，默认 30，最大 600"},
                },
                "required": ["command"],
            },
        },
    },
]


# --------------------------------------------------------------------------- #
# 公共小工具
# --------------------------------------------------------------------------- #
def resolve_in_root(root: Path, raw: str) -> Path:
    p = Path(raw)
    p = p if p.is_absolute() else (root / p)
    p = p.resolve()
    root = root.resolve()
    if not (p == root or root in p.parents):
        raise ToolError(f"路径越界：{raw} 不在工作区 {root} 内，请改用相对路径")
    return p


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    cut = len(text) - limit
    return f"{text[:head]}\n…（中间省略 {cut} 字符）…\n{text[-tail:]}"


def _atomic_write(path: Path, content: str) -> None:
    """临时文件 + rename：写一半崩了也不会留下半个文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _count_lines(text: str) -> int:
    return len(text.splitlines())


# --------------------------------------------------------------------------- #
# 四个工具的实现
# --------------------------------------------------------------------------- #
def tool_read(root: Path, path: str, offset: int = 1, limit: int = 400) -> ToolResult:
    p = resolve_in_root(root, path)
    if not p.exists():
        parent = p.parent if p.parent.exists() else root
        siblings = "、".join(sorted(f"{c.name}/" if c.is_dir() else c.name for c in parent.iterdir())[:40])
        raise ToolError(f"文件不存在：{path}；{parent.name or parent} 下可用：{siblings or '（空目录）'}")
    if p.is_dir():
        entries = "、".join(sorted(f"{c.name}/" if c.is_dir() else c.name for c in p.iterdir())[:60])
        return ToolResult(f"{path} 是目录，内容：{entries}")

    if p.suffix.lower() in IMAGE_SUFFIX:
        mime = IMAGE_SUFFIX[p.suffix.lower()]
        data = base64.b64encode(p.read_bytes()).decode("ascii")
        return ToolResult(f"图片 {path}（{p.stat().st_size} 字节）",
                          images=[f"data:{mime};base64,{data}"])

    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    start = max(1, int(offset))
    limit = max(1, int(limit))
    end = min(len(lines), start - 1 + limit)
    body = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(start, end + 1))
    tail = ""
    if end < len(lines):
        tail = f"\n\n（共 {len(lines)} 行，本次显示 {start}–{end} 行，还有 {len(lines) - end} 行，用 offset={end + 1} 继续读）"
    elif start > 1:
        tail = f"\n\n（共 {len(lines)} 行，本次显示 {start}–{end} 行）"
    return ToolResult(f"{path}（共 {len(lines)} 行）\n{body}{tail}")


def tool_write(root: Path, path: str, content: str) -> ToolResult:
    p = resolve_in_root(root, path)
    before = _count_lines(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else 0
    _atomic_write(p, content)
    after = _count_lines(content)
    if before:
        return ToolResult(f"已覆盖 {path}：原来 {before} 行 → 现在 {after} 行")
    return ToolResult(f"已新建 {path}：{after} 行")


def tool_edit(root: Path, path: str, old: str, new: str) -> ToolResult:
    p = resolve_in_root(root, path)
    if not p.exists():
        raise ToolError(f"文件不存在：{path}；先用 bash 看一下目录里有什么，或用 write 新建")
    text = p.read_text(encoding="utf-8", errors="replace")
    hits = text.count(old)
    if hits == 0:
        raise ToolError(
            f"在 {path} 里没找到 old 的原文（文件共 {_count_lines(text)} 行）。"
            "先用 read 拿到带行号的原文，注意缩进、空格和换行要完全一致。"
        )
    if hits > 1:
        rows, pos = [], 0
        while True:
            i = text.find(old, pos)
            if i < 0:
                break
            rows.append(text.count("\n", 0, i) + 1)
            pos = i + 1
        raise ToolError(
            f"old 在 {path} 中出现 {hits} 次（行号：{rows[:10]}），不许猜："
            "请补充上下文让 old 唯一，或拆成多次替换。"
        )
    _atomic_write(p, text.replace(old, new, 1))
    diff = "\n".join(difflib.unified_diff(
        text.splitlines(), text.replace(old, new, 1).splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=2))
    return ToolResult(f"已修改 {path}（1 处）\n{diff}")


def tool_bash(root: Path, settings: Settings, command: str, timeout: float | None = None) -> ToolResult:
    t = float(timeout if timeout else settings.bash_timeout)
    t = min(max(t, 1.0), 600.0)
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(root), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=t,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"命令超时（{t:.0f}s）：{command}；请缩小范围（如限定目录/文件），或显式加大 timeout")
    out = _truncate((proc.stdout or "").rstrip(), settings.max_output_chars)
    err = _truncate((proc.stderr or "").rstrip(), settings.max_output_chars)
    body = f"$ {command}\n[exit_code] {proc.returncode}"
    if out:
        body += f"\n[stdout]\n{out}"
    if err:
        body += f"\n[stderr]\n{err}"
    return ToolResult(body)  # 非 0 也是"正常返回"，模型要看得见才能自救


def dispatch(name: str, args: dict, settings: Settings) -> ToolResult:
    try:
        if name == "read":
            return tool_read(settings.root, args["path"],
                             args.get("offset", 1), args.get("limit", 400))
        if name == "write":
            return tool_write(settings.root, args["path"], args["content"])
        if name == "edit":
            return tool_edit(settings.root, args["path"], args["old"], args["new"])
        if name == "bash":
            return tool_bash(settings.root, settings, args["command"], args.get("timeout"))
        raise ToolError(f"没有这个工具：{name}；可用：read、write、edit、bash")
    except ToolError as e:
        return ToolResult(str(e), is_error=True)
    except KeyError as e:
        return ToolResult(f"缺少必要参数：{e.args[0]}", is_error=True)
    except Exception as e:  # 兜底：任何异常都要变成模型能读懂的一句话
        return ToolResult(f"{type(e).__name__}: {e}", is_error=True)
