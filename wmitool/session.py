"""连接管理：DCOM/WinRM 执行 + SMB 文件传输"""

from __future__ import annotations

import socket
import uuid
from io import BytesIO
from typing import Optional


class Session:
    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        protocol: str = "dcom",
        port: int = 135,
        ssl: bool = False,
        smb_port: int = 445,
        verbose: bool = False,
    ) -> None:
        self.host = host
        self.user = user
        self.password = password
        self.protocol = protocol
        self.port = port
        self.ssl = ssl
        self.smb_port = smb_port
        self.verbose = verbose

        self._winrm_protocol = None   # pywinrm Protocol 对象
        self._dcom = None             # impacket DCOMConnection
        self._iWbemServices = None    # WMI 服务接口
        self._smb_conn = None         # impacket SMBConnection
        self.smb_available = False

        # 解析域和用户名
        if "\\" in user:
            self.domain, self.username = user.split("\\", 1)
        elif "/" in user:
            self.domain, self.username = user.split("/", 1)
        else:
            self.domain = ""
            self.username = user

    # ------------------------------------------------------------------
    # 连接建立
    # ------------------------------------------------------------------

    def connect(self) -> None:
        if self.protocol == "winrm":
            self._connect_winrm()
        else:
            self._connect_dcom()
        self._probe_smb()

    def _connect_winrm(self) -> None:
        import winrm

        scheme = "https" if self.ssl else "http"
        endpoint = f"{scheme}://{self.host}:{self.port}/wsman"
        self._winrm_protocol = winrm.Protocol(
            endpoint=endpoint,
            transport="ntlm",
            username=self.user,
            password=self.password,
            server_cert_validation="ignore" if self.ssl else "validate",
        )
        # 建立测试 shell 验证连通性
        shell_id = self._winrm_protocol.open_shell()
        self._winrm_protocol.close_shell(shell_id)

    def _connect_dcom(self) -> None:
        from impacket.dcerpc.v5.dcom import wmi
        from impacket.dcerpc.v5.dcomrt import DCOMConnection
        from impacket.dcerpc.v5.dtypes import NULL

        self._dcom = DCOMConnection(
            self.host,
            self.username,
            self.password,
            self.domain,
            oxidResolver=True,
            doKerberos=False,
        )
        iInterface = self._dcom.CoCreateInstanceEx(
            wmi.CLSID_WbemLevel1Login, wmi.IID_IWbemLevel1Login
        )
        iWbemLevel1Login = wmi.IWbemLevel1Login(iInterface)
        self._iWbemServices = iWbemLevel1Login.NTLMLogin(
            "//./root/cimv2", NULL, NULL
        )
        iWbemLevel1Login.RemRelease()

    def _probe_smb(self) -> None:
        try:
            sock = socket.create_connection((self.host, self.smb_port), timeout=3)
            sock.close()
            self._connect_smb()
            self.smb_available = True
        except (OSError, Exception):
            if self.verbose:
                print(f"[verbose] SMB {self.smb_port} 不可达，将使用 WMI+Base64 传输")

    def _connect_smb(self) -> None:
        from impacket.smbconnection import SMBConnection

        self._smb_conn = SMBConnection(self.host, self.host, sess_port=self.smb_port)
        self._smb_conn.login(self.username, self.password, self.domain)

    def close(self) -> None:
        try:
            if self._smb_conn:
                self._smb_conn.logoff()
        except Exception:
            pass
        try:
            if self._dcom:
                self._dcom.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 命令执行
    # ------------------------------------------------------------------

    def execute(self, command: str, cwd: Optional[str] = None) -> tuple[str, str, int]:
        """返回 (stdout, stderr, exit_code)"""
        if self.protocol == "winrm":
            return self._execute_winrm(command, cwd)
        return self._execute_dcom(command, cwd)

    def _execute_winrm(self, command: str, cwd: Optional[str]) -> tuple[str, str, int]:
        import winrm

        if cwd:
            full_cmd = f"cd /d {cwd} && {command}"
        else:
            full_cmd = command

        shell_id = self._winrm_protocol.open_shell()
        try:
            cmd_id = self._winrm_protocol.run_command(shell_id, "cmd", ["/c", full_cmd])
            stdout, stderr, rc = self._winrm_protocol.get_command_output(shell_id, cmd_id)
            self._winrm_protocol.cleanup_command(shell_id, cmd_id)
        finally:
            self._winrm_protocol.close_shell(shell_id)

        return (
            stdout.decode("gbk", errors="replace"),
            stderr.decode("gbk", errors="replace"),
            rc,
        )

    def _execute_dcom(self, command: str, cwd: Optional[str]) -> tuple[str, str, int]:
        from impacket.dcerpc.v5.dtypes import NULL
        import time

        uid = uuid.uuid4().hex[:8]
        out_file = f"C:\\Windows\\Temp\\out_{uid}.tmp"
        exit_file = f"C:\\Windows\\Temp\\exit_{uid}.tmp"
        out_smb = f"\\Windows\\Temp\\out_{uid}.tmp"
        exit_smb = f"\\Windows\\Temp\\exit_{uid}.tmp"

        if cwd:
            user_cmd = f"cd /d {cwd} && {command} > {out_file} 2>&1"
        else:
            user_cmd = f"{command} > {out_file} 2>&1"

        # /V:on 启用延迟展开，!ERRORLEVEL! 在执行时求值，正确捕获退出码
        full_cmd = f"cmd.exe /V:on /Q /c {user_cmd} & echo !ERRORLEVEL! > {exit_file}"

        win32_process, _ = self._iWbemServices.GetObject("Win32_Process")
        try:
            win32_process.Create(full_cmd, NULL, NULL)
        except Exception:
            # impacket 某些版本在解析 Create 响应时抛 ENCODED_STRING 错误，
            # 但进程已成功启动，忽略即可
            pass

        # 等待 exit 文件出现（表示命令已执行完毕），最长等 30s
        rc = 0
        for _ in range(60):
            time.sleep(0.5)
            try:
                exit_data = self.smb_get_file("C$", exit_smb)
                rc_str = exit_data.decode("gbk", errors="replace").strip()
                try:
                    rc = int(rc_str)
                except ValueError:
                    rc = 0
                try:
                    self.smb_rm("C$", exit_smb)
                except Exception:
                    pass
                break
            except Exception:
                continue
        else:
            return "", "命令超时或输出文件不可读", 1

        stdout = ""
        try:
            out_data = self.smb_get_file("C$", out_smb)
            stdout = out_data.decode("gbk", errors="replace")
            try:
                self.smb_rm("C$", out_smb)
            except Exception:
                pass
        except Exception:
            pass

        return stdout, "", rc

    # ------------------------------------------------------------------
    # WinRM Bootstrap（供 PTY shell 使用）
    # ------------------------------------------------------------------

    def bootstrap_winrm(self) -> bool:
        """通过 DCOM 连接尝试在目标机器上启用 WinRM，返回 True 表示 5985 可达。"""
        # 步骤 1：PowerShell Enable-PSRemoting（一条命令含服务、监听器、防火墙）
        try:
            self.execute(
                'powershell -Command "Enable-PSRemoting -Force -SkipNetworkProfileCheck"'
            )
        except Exception:
            pass
        if self._probe_winrm():
            return True

        # 步骤 2：纯 cmd 回退（PowerShell 被杀时）
        for cmd in [
            "sc config WinRM start= auto",
            "sc start WinRM",
            "winrm quickconfig -quiet",
            'netsh advfirewall firewall add rule name="WinRM-wmitool"'
            " dir=in action=allow protocol=TCP localport=5985",
        ]:
            try:
                self.execute(cmd)
            except Exception:
                pass
        return self._probe_winrm()

    def _probe_winrm(self) -> bool:
        """TCP 探测 5985，重试间隔 1s，最长等待 15s。"""
        import time

        for _ in range(15):
            try:
                conn = socket.create_connection((self.host, 5985), timeout=1)
                conn.close()
                return True
            except OSError:
                time.sleep(1)
        return False

    # ------------------------------------------------------------------
    # SMB 文件操作（供 sftp 模块使用）
    # ------------------------------------------------------------------

    def smb_list(self, share: str, path: str) -> list:
        return self._smb_conn.listPath(share, path)

    def smb_get_file(self, share: str, remote_path: str) -> bytes:
        buf = BytesIO()
        self._smb_conn.getFile(share, remote_path, buf.write)
        return buf.getvalue()

    def smb_put_file(self, share: str, remote_path: str, data: bytes) -> None:
        self._smb_conn.putFile(share, remote_path, BytesIO(data).read)

    def smb_mkdir(self, share: str, path: str) -> None:
        self._smb_conn.createDirectory(share, path)

    def smb_rm(self, share: str, path: str) -> None:
        self._smb_conn.deleteFiles(share, path)

    # ------------------------------------------------------------------
    # 内部辅助：通过 SMB 读写 %TEMP% 目录
    # ------------------------------------------------------------------

    def _smb_read_temp(self, filename: str) -> str:
        # TEMP 通常在 C:\Windows\Temp 或用户 Temp，尝试常见位置
        for share, path in [
            ("C$", "\\Windows\\Temp\\" + filename),
            ("C$", "\\Users\\Administrator\\AppData\\Local\\Temp\\" + filename),
        ]:
            try:
                data = self.smb_get_file(share, path)
                return data.decode("gbk", errors="replace")
            except Exception:
                continue
        raise FileNotFoundError(f"临时文件 {filename} 未找到")

    def _smb_delete_temp(self, filename: str) -> None:
        for share, path in [
            ("C$", "\\Windows\\Temp\\" + filename),
            ("C$", "\\Users\\Administrator\\AppData\\Local\\Temp\\" + filename),
        ]:
            try:
                self.smb_rm(share, path)
                return
            except Exception:
                continue

    # ------------------------------------------------------------------
    # WMI+Base64 文件传输（SMB 不可用时的回退）
    # ------------------------------------------------------------------

    def wmi_put_file(self, remote_path: str, data: bytes) -> None:
        import base64

        chunk_size = 250 * 1024
        # 先清空/创建目标文件
        self.execute(f'type nul > "{remote_path}"')
        for i in range(0, len(data), chunk_size):
            chunk = base64.b64encode(data[i : i + chunk_size]).decode()
            ps_cmd = (
                f"[IO.File]::AppendAllBytes('{remote_path}',"
                f"[Convert]::FromBase64String('{chunk}'))"
            )
            self.execute(f'powershell -Command "{ps_cmd}"')

    def wmi_get_file(self, remote_path: str) -> bytes:
        import base64

        out, _, _ = self.execute(
            f'powershell -Command "[Convert]::ToBase64String([IO.File]::ReadAllBytes(\'{remote_path}\'))"'
        )
        return base64.b64decode(out.strip())
