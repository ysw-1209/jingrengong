"""桌面智能体的后端：循环 + 四个工具。

用法（后续 FastAPI/UI 都复用同一套）：
    from agent.config import Settings
    from agent.loop import run_agent

    settings = Settings.from_env()
    for ev in run_agent("帮我跑一下测试", settings):
        print(ev)
"""

from .config import Settings
from .loop import run_agent
from .tools import SCHEMAS, dispatch

__all__ = ["Settings", "run_agent", "SCHEMAS", "dispatch"]
__version__ = "0.1.0"
