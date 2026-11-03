"""配置：只读环境变量，不依赖第三方库。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def user_env_path() -> Path:
    """打包后 cwd 不可写，Key 存到用户目录；开发时也用同一份。"""
    return Path(os.environ.get("AGENT_USER_ENV")
                or Path.home() / ".desktop-agent" / ".env")


def load_dotenv(path: str | Path = ".env") -> None:
    """极简 .env 加载：已存在的环境变量优先，绝不覆盖。

    顺序：项目 .env → 用户目录 .env。项目里的值优先，
    这样换台机器 clone 下来仍然能覆盖个人配置。
    """
    for p in (Path(path), user_env_path()):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Settings:
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"

    root: Path = field(default_factory=Path.cwd)  # 工具只能在这个目录里动文件
    max_steps: int = 40          # 护栏一：步数
    token_budget: int = 400_000  # 护栏二：预算
    overall_timeout: float = 900.0  # 护栏三：超时（整轮，秒）

    step_timeout: float = 120.0  # 单次模型请求超时
    bash_timeout: float = 30.0   # bash 工具默认超时
    max_output_chars: int = 20_000  # 工具输出截断长度

    @classmethod
    def from_env(cls, root: str | Path | None = None) -> "Settings":
        load_dotenv()
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            root=Path(root or os.environ.get("AGENT_ROOT") or Path.cwd()).resolve(),
            max_steps=int(os.environ.get("AGENT_MAX_STEPS", "40")),
            token_budget=int(os.environ.get("AGENT_TOKEN_BUDGET", "400000")),
            overall_timeout=float(os.environ.get("AGENT_OVERALL_TIMEOUT", "900")),
            step_timeout=float(os.environ.get("AGENT_STEP_TIMEOUT", "120")),
            bash_timeout=float(os.environ.get("AGENT_BASH_TIMEOUT", "30")),
            max_output_chars=int(os.environ.get("AGENT_MAX_OUTPUT_CHARS", "20000")),
        )
