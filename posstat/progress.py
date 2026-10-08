"""実行中表示。

TTY では rich.progress の複数バー表示(パーセンテージ / ETA 内蔵)。
非TTY環境(リダイレクト・CI)では rich が自動でバー描画を抑制するため、
代わりに log_interval 秒ごとの行ログを標準エラーに出す。
"""

from __future__ import annotations

import time
from typing import Optional

from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TimeRemainingColumn


class Reporter:
    """rich Progress の薄いラッパ。非TTY では定期的に行ログを出す。"""

    def __init__(self, log_interval: float = 30.0):
        self._console = Console(stderr=True)
        self._log_interval = max(1.0, float(log_interval))
        self._last_log = 0.0
        self._progress = Progress(
            "[bold]{task.description}",
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=self._console,
        )

    def __enter__(self) -> "Reporter":
        self._progress.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        self._progress.__exit__(*exc)

    def add_task(self, description: str, total: Optional[float], start: bool = True) -> int:
        return self._progress.add_task(description, total=total, start=start)

    def start_task(self, task_id: int, total: Optional[float] = None) -> None:
        if total is not None:
            self._progress.update(task_id, total=total)
        self._progress.start_task(task_id)

    def advance(self, task_id: int, n: float = 1) -> None:
        self._progress.advance(task_id, n)
        self._log(task_id)

    def finish(self, task_id: int) -> None:
        task = self._progress.tasks[task_id]
        if task.total is not None:
            self._progress.update(task_id, completed=task.total)
        self._log(task_id, force=True)

    def _log(self, task_id: int, force: bool = False) -> None:
        """非TTY のときだけ、log_interval 秒ごと(と各段の完了時)に1行出す。"""
        if self._console.is_terminal:
            return
        now = time.monotonic()
        if not force and now - self._last_log < self._log_interval:
            return
        self._last_log = now
        t = self._progress.tasks[task_id]
        done = f"{int(t.completed)}/{int(t.total)} ({t.percentage:.0f}%)" if t.total \
            else f"{int(t.completed)}"
        self._console.print(f"[posstat] {t.description}: {done}", markup=False)
