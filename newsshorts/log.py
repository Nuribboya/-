from __future__ import annotations

import threading
from collections import deque
from datetime import datetime

# Recent lines kept in memory so the app can show what the watcher is doing.
_lines: deque[str] = deque(maxlen=300)
_lock = threading.Lock()


def log(message: str) -> None:
    line = f"{datetime.now().strftime('%H:%M:%S')} {message}"
    print(line, flush=True)
    with _lock:
        _lines.append(line)


def recent_lines(limit: int = 100) -> list[str]:
    with _lock:
        return list(_lines)[-limit:]
