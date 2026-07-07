"""実行中表示。

TTY では rich.progress の複数バー表示(パーセンテージ / ETA 内蔵)。
非TTY環境(リダイレクト・CI)では rich が自動でバー描画を抑制するため、
代わりに log_interval 秒ごとの行ログを標準エラーに出す。
"""

from __future__ import annotations

import sys
import time
from typing import Dict, Optional


class Reporter:
    """rich Progress の薄いラッパ。非TTYでは行ログにフォールバックする。"""

    def __init__(self, log_interval: float = 30.0):
        from rich.console import Console

        self._console = Console(stderr=True)
        self._is_tty = self._console.is_terminal
        self._log_interval = max(1.0, float(log_interval))
        self._progress = None
        self._tasks: Dict[int, dict] = {}
        self._next_id = 0
        self._last_log = 0.0

    def __enter__(self) -> "Reporter":
        if self._is_tty:
            from rich.progress import (
                BarColumn,
                Progress,
                TaskProgressColumn,
                TimeRemainingColumn,
            )

            self._progress = Progress(
                "[bold]{task.description}",
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=self._console,
            )
            self._progress.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        if self._progress is not None:
            self._progress.__exit__(*exc)

    def add_task(self, description: str, total: Optional[float], start: bool = True) -> int:
        if self._progress is not None:
            return self._progress.add_task(description, total=total, start=start)
        task_id = self._next_id
        self._next_id += 1
        self._tasks[task_id] = {"desc": description, "total": total, "done": 0.0}
        return task_id

    def start_task(self, task_id: int, total: Optional[float] = None) -> None:
        if self._progress is not None:
            if total is not None:
                self._progress.update(task_id, total=total)
            self._progress.start_task(task_id)
            return
        if total is not None:
            self._tasks[task_id]["total"] = total

    def advance(self, task_id: int, n: float = 1) -> None:
        if self._progress is not None:
            self._progress.advance(task_id, n)
            return
        t = self._tasks[task_id]
        t["done"] += n
        now = time.monotonic()
        if now - self._last_log >= self._log_interval:
            self._last_log = now
            self._log(t)

    def finish(self, task_id: int) -> None:
        if self._progress is not None:
            task = self._progress.tasks[task_id]
            if task.total is not None:
                self._progress.update(task_id, completed=task.total)
            return
        t = self._tasks[task_id]
        if t["total"] is not None:
            t["done"] = t["total"]
        self._log(t)

    @staticmethod
    def _log(t: dict) -> None:
        total = t["total"]
        if total:
            pct = 100.0 * t["done"] / total
            print(f"[posstat] {t['desc']}: {int(t['done'])}/{int(total)} ({pct:.0f}%)", file=sys.stderr)
        else:
            print(f"[posstat] {t['desc']}: {int(t['done'])}", file=sys.stderr)
