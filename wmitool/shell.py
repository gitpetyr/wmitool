"""交互式 Shell：prompt_toolkit 输入 + 命令历史 + 路径跟踪"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory

if TYPE_CHECKING:
    from wmitool.session import Session


def _query_cwd(session: "Session") -> str:
    out, _, _ = session.execute("cd")
    return out.strip() or "C:\\"


def _update_cwd(session: "Session", cwd: str, command: str) -> str:
    """如果命令是 cd，执行后重新查询当前路径"""
    stripped = command.strip()
    if stripped.lower() == "cd" or stripped.lower().startswith("cd ") or stripped.lower().startswith("cd\t"):
        out, _, _ = session.execute("cd", cwd=cwd)
        new_cwd = out.strip()
        return new_cwd if new_cwd else cwd
    return cwd


def run_shell(session: "Session") -> None:
    history = InMemoryHistory()
    prompt_session: PromptSession = PromptSession(history=history)

    try:
        cwd = _query_cwd(session)
    except Exception:
        cwd = "C:\\"

    print(f"已连接到 {session.host}（{session.protocol.upper()}）。输入 exit 退出。")

    while True:
        # 构造提示符
        display_user = session.user.split("\\")[-1] if "\\" in session.user else session.user
        prompt_text = f"[{display_user}@{session.host}] {cwd}> "

        try:
            command = prompt_session.prompt(prompt_text)
        except (EOFError, KeyboardInterrupt):
            print()
            break

        command = command.strip()
        if not command:
            continue

        if command.lower() in ("exit", "quit"):
            break

        try:
            stdout, stderr, _ = session.execute(command, cwd=cwd)
        except Exception as exc:
            print(f"执行错误: {exc}", file=sys.stderr)
            continue

        if stdout:
            print(stdout, end="" if stdout.endswith("\n") else "\n")
        if stderr:
            print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)

        # 更新工作目录
        cwd = _update_cwd(session, cwd, command)

    session.close()
