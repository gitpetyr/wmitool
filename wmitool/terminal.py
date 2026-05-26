"""本地终端控制：raw mode + SIGWINCH 处理"""

from __future__ import annotations

import queue
import signal
import sys
import termios
import tty
from typing import Optional


class RawTerminal:
    """进入时设 raw mode，退出时恢复，注册 SIGWINCH handler"""

    def __init__(self) -> None:
        self._old_settings: Optional[list] = None
        self._old_sigwinch = None
        self.winsize_queue: queue.Queue[tuple[int, int]] = queue.Queue()
        self.enabled = sys.stdin.isatty()

    def __enter__(self) -> "RawTerminal":
        if self.enabled:
            fd = sys.stdin.fileno()
            self._old_settings = termios.tcgetattr(fd)
            tty.setraw(fd)
            self._old_sigwinch = signal.signal(signal.SIGWINCH, self._handle_sigwinch)
        return self

    def __exit__(self, *args) -> None:
        if self.enabled and self._old_settings is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_settings)
        if self._old_sigwinch is not None:
            signal.signal(signal.SIGWINCH, self._old_sigwinch)

    def _handle_sigwinch(self, signum, frame) -> None:
        self.winsize_queue.put(self.get_size())

    def get_size(self) -> tuple[int, int]:
        """返回 (cols, rows)"""
        import fcntl
        import struct
        try:
            buf = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
            rows, cols = struct.unpack("hh", buf[:4])
            return cols, rows
        except Exception:
            return 80, 24
