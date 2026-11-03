"""增量 C（选做）：让 agent 从"关掉终端就死"变成"能被唤醒、能续跑"。

最小实现两块：
1. checkpoint：每一步落盘，重启后从断点继续，而不是从第 1 步重来。
2. routines：routines.yaml 定时触发，触发过就记 last_run，重复唤醒不会重复执行。

说明：这里的 step 是可注入的（默认写一个幂等的状态文件），
所以 `python -m agent.inspect daemon` 能在 2 秒内离线跑完，
不需要模型、不需要等一小时。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

STATE_DIR = ".agent"
CHECKPOINT = "checkpoint.json"


class Killed(Exception):
    """模拟 kill -9：状态留在盘上，进程直接没了。"""


# --------------------------------------------------------------------------- #
# checkpoint
# --------------------------------------------------------------------------- #
def _cp_path(root: Path) -> Path:
    return root / STATE_DIR / CHECKPOINT


def load_checkpoint(root: Path) -> dict:
    path = _cp_path(root)
    if not path.exists():
        return {"step": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"step": 0}


def save_checkpoint(root: Path, data: dict) -> None:
    path = _cp_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)          # 原子写：不能写出半个 checkpoint


def step_work(root: Path, i: int) -> None:
    """一步的真实动作。必须幂等：重复执行结果一样。"""
    p = root / STATE_DIR / "steps" / f"{i}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"step": i, "ts": int(time.time())}), encoding="utf-8")


def run_steps(root: Path, total: int, kill_at: int | None = None) -> tuple[int, int]:
    """返回 (起始步, 恢复步)。kill_at 用来模拟中途崩溃。"""
    cp = load_checkpoint(root)
    start = int(cp.get("step", 0))
    for i in range(start + 1, total + 1):
        if kill_at is not None and i == kill_at:
            raise Killed(f"模拟 kill -9：第 {i} 步之前进程没了")
        step_work(root, i)
        cp["step"] = i
        save_checkpoint(root, cp)
    return start, start + 1


# --------------------------------------------------------------------------- #
# routines
# --------------------------------------------------------------------------- #
@dataclass
class Routine:
    name: str
    every_seconds: int
    enabled: bool = True
    last_run: float = 0.0

    def due(self, now: float) -> bool:
        return self.enabled and (now - self.last_run) >= self.every_seconds


def load_routines(root: Path) -> list[Routine]:
    path = root / "routines.yaml"
    if not path.exists():
        return []
    conf = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for item in conf.get("routines") or []:
        out.append(Routine(
            name=str(item.get("name") or "unnamed"),
            every_seconds=int(item.get("every_seconds") or 3600),
            enabled=bool(item.get("enabled", True)),
            last_run=float(item.get("last_run") or 0),
        ))
    return out


def save_routines(root: Path, routines: list[Routine]) -> None:
    path = root / "routines.yaml"
    data = {"routines": [
        {"name": r.name, "every_seconds": r.every_seconds,
         "enabled": r.enabled, "last_run": r.last_run}
        for r in routines
    ]}
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def fire_due(root: Path, now: float | None = None) -> list[str]:
    """到点就跑，跑完记 last_run——这样同一个唤醒不会重复执行（幂等）。"""
    now = time.time() if now is None else now
    routines = load_routines(root)
    fired = []
    for r in routines:
        if r.due(now):
            fired.append(r.name)
            r.last_run = now
    if fired:
        save_routines(root, routines)
    return fired


def notify(root: Path, text: str) -> bool:
    """最小通知：写日志 + 终端响铃。托盘/系统通知会更好，但机制一样。"""
    try:
        path = root / STATE_DIR / "notifications.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        print("\a", end="", flush=True)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# 验收
# --------------------------------------------------------------------------- #
ROUTINES_YAML = """routines:
  - name: 每日自检
    every_seconds: 86400
  - name: 每小时看 diff
    every_seconds: 3600
  - name: 每周发版
    every_seconds: 604800
    enabled: false
"""


def run_report() -> dict:
    import shutil
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="daemon_"))
    try:
        # 1) 续跑：前 3 步落盘，第 4 步前"崩"，重启后从第 4 步继续
        killed = False
        try:
            run_steps(root, 5, kill_at=4)
        except Killed:
            killed = True
        _, resume_step = run_steps(root, 5)
        done = load_checkpoint(root).get("step", 0)

        # 2) 定时触发：首次 2 条到点，第二次 0 条（幂等）
        (root / "routines.yaml").write_text(ROUTINES_YAML, encoding="utf-8")
        fired_first = fire_due(root)
        fired_second = fire_due(root)

        # 3) 通知
        notified = notify(root, "长任务已完成")

        return {
            "triggers_fired": len(fired_first),
            "resume_from_step": resume_step if killed else 0,
            "notify_fired": notified,
            "idempotent": len(fired_second) == 0 and done == 5,
            "detail": {
                "killed": killed,
                "steps_done_after_resume": done,
                "first_fired": fired_first,
                "second_fired": fired_second,
            },
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)
