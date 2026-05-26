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
            "//./root/cimv2", None, None
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
        from impacket.dcerpc.v5.dcom import wmi

        tmp_file = f"__wmitool_{uuid.uuid4().hex[:8]}.tmp"
        tmp_path = f"%TEMP%\\{tmp_file}"

        if cwd:
            full_cmd = f"cd /d {cwd} && {command} > {tmp_path} 2>&1"
        else:
            full_cmd = f"{command} > {tmp_path} 2>&1"

        win32_process, _ = self._iWbemServices.GetObject("Win32_Process")
        win32_process.Create(f"cmd.exe /Q /c {full_cmd}", None, None)

        # 等待输出文件出现并读取
        import time

        for _ in range(30):
            time.sleep(0.5)
            try:
                content = self._smb_read_temp(tmp_file)
                self._smb_delete_temp(tmp_file)
                return content, "", 0
            except Exception:
                continue

        return "", "命令超时或输出文件不可读", 1

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
