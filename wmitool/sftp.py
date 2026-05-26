"""SFTP 模式：SMB 优先传输，回退 WMI+Base64"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory

if TYPE_CHECKING:
    from wmitool.session import Session


def _unc_to_smb(path: str) -> tuple[str, str]:
    """将 Windows 路径转换为 (share, smb_path)，如 C:\foo\bar -> ('C$', '\\foo\\bar')"""
    path = path.replace("/", "\\")
    if len(path) >= 2 and path[1] == ":":
        drive = path[0].upper()
        rest = path[2:] or "\\"
        return f"{drive}$", rest
    # 已经是相对路径，使用 C$
    return "C$", "\\" + path.lstrip("\\")


def _join_remote(cwd: str, name: str) -> str:
    if os.path.isabs(name) or (len(name) >= 2 and name[1] == ":"):
        return name
    return cwd.rstrip("\\") + "\\" + name


# ------------------------------------------------------------------
# 命令实现
# ------------------------------------------------------------------


def cmd_ls(session: "Session", remote_cwd: str, args: list[str]) -> None:
    path = args[0] if args else remote_cwd
    path = _join_remote(remote_cwd, path)
    if session.smb_available:
        share, smb_path = _unc_to_smb(path)
        pattern = smb_path.rstrip("\\") + "\\*"
        try:
            entries = session.smb_list(share, pattern)
            for e in entries:
                name = e.get_longname()
                if name in (".", ".."):
                    continue
                size = e.get_filesize()
                is_dir = e.is_directory()
                tag = "<DIR>" if is_dir else f"{size:>12}"
                print(f"  {tag}  {name}")
        except Exception as exc:
            print(f"ls 失败: {exc}", file=sys.stderr)
    else:
        out, err, _ = session.execute(f'dir "{path}"', cwd=remote_cwd)
        if out:
            print(out)
        if err:
            print(err, file=sys.stderr)


def cmd_get(session: "Session", remote_cwd: str, local_cwd: str, args: list[str]) -> None:
    if not args:
        print("用法: get <远程文件> [本地路径]", file=sys.stderr)
        return
    remote_path = _join_remote(remote_cwd, args[0])
    local_path = args[1] if len(args) > 1 else os.path.basename(args[0])
    local_path = os.path.join(local_cwd, local_path)

    try:
        if session.smb_available:
            share, smb_path = _unc_to_smb(remote_path)
            data = session.smb_get_file(share, smb_path)
        else:
            data = session.wmi_get_file(remote_path)
        with open(local_path, "wb") as f:
            f.write(data)
        print(f"已下载: {remote_path} -> {local_path} ({len(data)} 字节)")
    except Exception as exc:
        print(f"get 失败: {exc}", file=sys.stderr)


def cmd_put(session: "Session", remote_cwd: str, local_cwd: str, args: list[str]) -> None:
    if not args:
        print("用法: put <本地文件> [远程路径]", file=sys.stderr)
        return
    local_path = os.path.join(local_cwd, args[0])
    remote_name = args[1] if len(args) > 1 else os.path.basename(args[0])
    remote_path = _join_remote(remote_cwd, remote_name)

    if not os.path.isfile(local_path):
        print(f"本地文件不存在: {local_path}", file=sys.stderr)
        return

    try:
        with open(local_path, "rb") as f:
            data = f.read()
        if session.smb_available:
            share, smb_path = _unc_to_smb(remote_path)
            session.smb_put_file(share, smb_path, data)
        else:
            session.wmi_put_file(remote_path, data)
        print(f"已上传: {local_path} -> {remote_path} ({len(data)} 字节)")
    except Exception as exc:
        print(f"put 失败: {exc}", file=sys.stderr)


def cmd_mkdir(session: "Session", remote_cwd: str, args: list[str]) -> None:
    if not args:
        print("用法: mkdir <路径>", file=sys.stderr)
        return
    path = _join_remote(remote_cwd, args[0])
    try:
        if session.smb_available:
            share, smb_path = _unc_to_smb(path)
            session.smb_mkdir(share, smb_path)
        else:
            session.execute(f'mkdir "{path}"', cwd=remote_cwd)
        print(f"已创建目录: {path}")
    except Exception as exc:
        print(f"mkdir 失败: {exc}", file=sys.stderr)


def cmd_rm(session: "Session", remote_cwd: str, args: list[str]) -> None:
    if not args:
        print("用法: rm <路径>", file=sys.stderr)
        return
    path = _join_remote(remote_cwd, args[0])
    try:
        if session.smb_available:
            share, smb_path = _unc_to_smb(path)
            session.smb_rm(share, smb_path)
        else:
            session.execute(f'del /f /q "{path}"', cwd=remote_cwd)
        print(f"已删除: {path}")
    except Exception as exc:
        print(f"rm 失败: {exc}", file=sys.stderr)


def cmd_cd(remote_cwd: str, args: list[str]) -> str:
    if not args:
        print("用法: cd <路径>", file=sys.stderr)
        return remote_cwd
    new_path = args[0]
    if os.path.isabs(new_path) or (len(new_path) >= 2 and new_path[1] == ":"):
        return new_path.rstrip("\\") or new_path
    return remote_cwd.rstrip("\\") + "\\" + new_path.strip("\\")


def cmd_pwd(remote_cwd: str) -> None:
    print(remote_cwd)


def cmd_lls(local_cwd: str, args: list[str]) -> None:
    path = os.path.join(local_cwd, args[0]) if args else local_cwd
    try:
        entries = sorted(os.listdir(path))
        for name in entries:
            full = os.path.join(path, name)
            tag = "<DIR>" if os.path.isdir(full) else f"{os.path.getsize(full):>12}"
            print(f"  {tag}  {name}")
    except Exception as exc:
        print(f"lls 失败: {exc}", file=sys.stderr)


def cmd_lcd(local_cwd: str, args: list[str]) -> str:
    if not args:
        print("用法: lcd <路径>", file=sys.stderr)
        return local_cwd
    new_path = os.path.expanduser(args[0])
    if not os.path.isabs(new_path):
        new_path = os.path.join(local_cwd, new_path)
    new_path = os.path.normpath(new_path)
    if not os.path.isdir(new_path):
        print(f"本地目录不存在: {new_path}", file=sys.stderr)
        return local_cwd
    return new_path


HELP_TEXT = """\
可用命令:
  ls [路径]          列出远程目录
  get <远程> [本地]  下载文件
  put <本地> [远程]  上传文件
  mkdir <路径>       创建远程目录
  rm <路径>          删除远程文件
  cd <路径>          切换远程目录
  pwd                显示远程当前目录
  lls [路径]         列出本地目录
  lcd <路径>         切换本地目录
  exit / quit        退出
"""


def run_sftp(session: "Session") -> None:
    history = InMemoryHistory()
    prompt_session: PromptSession = PromptSession(history=history)

    # 初始化远程路径
    try:
        out, _, _ = session.execute("cd")
        remote_cwd = out.strip() or "C:\\"
    except Exception:
        remote_cwd = "C:\\"

    local_cwd = os.getcwd()

    transport = "SMB" if session.smb_available else "WMI+Base64"
    print(f"已连接到 {session.host}（{session.protocol.upper()}, 文件传输: {transport}）。输入 help 查看命令。")

    while True:
        prompt_text = f"sftp [{session.host}:{remote_cwd}]> "
        try:
            line = prompt_session.prompt(prompt_text)
        except (EOFError, KeyboardInterrupt):
            print()
            break

        parts = line.strip().split()
        if not parts:
            continue

        cmd, *args = parts

        if cmd in ("exit", "quit"):
            break
        elif cmd == "help":
            print(HELP_TEXT)
        elif cmd == "ls":
            cmd_ls(session, remote_cwd, args)
        elif cmd == "get":
            cmd_get(session, remote_cwd, local_cwd, args)
        elif cmd == "put":
            cmd_put(session, remote_cwd, local_cwd, args)
        elif cmd == "mkdir":
            cmd_mkdir(session, remote_cwd, args)
        elif cmd == "rm":
            cmd_rm(session, remote_cwd, args)
        elif cmd == "cd":
            remote_cwd = cmd_cd(remote_cwd, args)
        elif cmd == "pwd":
            cmd_pwd(remote_cwd)
        elif cmd == "lls":
            cmd_lls(local_cwd, args)
        elif cmd == "lcd":
            local_cwd = cmd_lcd(local_cwd, args)
        else:
            print(f"未知命令: {cmd}。输入 help 查看可用命令。", file=sys.stderr)

    session.close()
