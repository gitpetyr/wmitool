import argparse
import sys

from wmitool.session import Session
from wmitool.pty_shell import run_pty_shell
from wmitool.sftp import run_sftp


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("host", help="目标主机名或 IP")
    parser.add_argument("-u", "--user", required=True, help="用户名，支持 DOMAIN\\user 格式")
    parser.add_argument(
        "-p", "--password",
        nargs="?", const="", default="",
        help="密码（允许空字符串，-p 不带值等同于空密码）",
    )
    parser.add_argument(
        "--protocol",
        choices=["dcom", "winrm"],
        default="dcom",
        help="连接协议（默认 dcom）",
    )
    parser.add_argument("--port", type=int, default=None, help="连接端口（随协议自动切换默认值）")
    parser.add_argument("--ssl", action="store_true", help="WinRM 模式下启用 HTTPS")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细调试信息")


def _resolve_port(args: argparse.Namespace) -> int:
    if args.port is not None:
        return args.port
    if args.protocol == "winrm":
        return 5986 if args.ssl else 5985
    return 135


def cmd_ssh(args: argparse.Namespace) -> int:
    port = _resolve_port(args)
    try:
        session = Session(
            host=args.host,
            user=args.user,
            password=args.password,
            protocol=args.protocol,
            port=port,
            ssl=args.ssl,
            verbose=args.verbose,
        )
        session.connect()
    except Exception as exc:
        print(f"连接失败: {exc}", file=sys.stderr)
        return 1
    run_pty_shell(session)
    return 0


def cmd_sftp(args: argparse.Namespace) -> int:
    port = _resolve_port(args)
    try:
        session = Session(
            host=args.host,
            user=args.user,
            password=args.password,
            protocol=args.protocol,
            port=port,
            ssl=args.ssl,
            smb_port=args.smb_port,
            verbose=args.verbose,
        )
        session.connect()
    except Exception as exc:
        print(f"连接失败: {exc}", file=sys.stderr)
        return 1
    run_sftp(session)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wmitool",
        description="通过 WMI/DCOM 或 WinRM 连接远程 Windows 机器",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ssh_parser = subparsers.add_parser("ssh", help="交互式 Shell")
    _add_common_args(ssh_parser)
    ssh_parser.set_defaults(func=cmd_ssh)

    sftp_parser = subparsers.add_parser("sftp", help="文件传输")
    _add_common_args(sftp_parser)
    sftp_parser.add_argument("--smb-port", type=int, default=445, help="SMB 端口（默认 445）")
    sftp_parser.set_defaults(func=cmd_sftp)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))
