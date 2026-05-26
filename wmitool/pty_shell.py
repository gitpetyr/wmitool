"""PTY Shell 统一调度入口：WinRM PTY 或 DCOM batch fallback。"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wmitool.session import Session


def run_pty_shell(session: "Session") -> None:
    from wmitool.shell import run_shell
    from wmitool.winrm_pty import run_winrm_pty

    if not sys.stdin.isatty():
        run_shell(session)
        return

    if session.protocol == "winrm":
        run_winrm_pty(session)
        return

    # DCOM 路径：尝试 bootstrap WinRM
    print(f"[*] 尝试通过 DCOM 启用 WinRM ({session.host}:5985)...")
    if session.bootstrap_winrm():
        from wmitool.session import Session as _Session

        winrm_session = _Session(
            host=session.host,
            user=session.user,
            password=session.password,
            protocol="winrm",
            port=5985,
        )
        try:
            winrm_session.connect()
        except Exception as exc:
            print(f"[!] WinRM 连接失败: {exc}，降级到 DCOM batch shell")
            run_shell(session)
            return
        run_winrm_pty(winrm_session)
    else:
        print("[!] WinRM 不可达，降级到 DCOM batch shell（无实时回显）")
        run_shell(session)
