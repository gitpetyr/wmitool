"""流式 WinRM PTY：自定义 Receive / Signal + 交互主循环"""

from __future__ import annotations

import base64
import os
import select
import sys
import threading
import uuid
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    from wmitool.session import Session

# WS-Man 命名空间
_SOAP = "http://www.w3.org/2003/05/soap-envelope"
_WSA = "http://schemas.xmlsoap.org/ws/2004/08/addressing"
_WSMAN = "http://schemas.dmtf.org/wbem/wsman/1/wsman.xsd"
_WSMV = "http://schemas.microsoft.com/wbem/wsman/1/wsman.xsd"
_RSP = "http://schemas.microsoft.com/wbem/wsman/1/windows/shell"
_ANON = "http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous"
_RES_URI = "http://schemas.microsoft.com/wbem/wsman/1/windows/shell/cmd"
_ACT_RECV = "http://schemas.microsoft.com/wbem/wsman/1/windows/shell/Receive"
_ACT_SIG = "http://schemas.microsoft.com/wbem/wsman/1/windows/shell/Signal"
_CTRL_C = "http://schemas.microsoft.com/wbem/wsman/1/windows/shell/signal/ctrl_c"
_STATE_DONE = "http://schemas.microsoft.com/wbem/wsman/1/windows/shell/CommandState/Done"

_TIMEOUT_KEYWORDS = ("TimedOut", "timed out", "timeout", "Timeout", "OperationTimeout", "WSManFault")


def _soap_envelope(endpoint: str, shell_id: str, action: str, timeout: str, body_xml: str) -> str:
    msg_id = f"uuid:{uuid.uuid4()}"
    return (
        f'<env:Envelope'
        f' xmlns:env="{_SOAP}"'
        f' xmlns:wsa="{_WSA}"'
        f' xmlns:wsman="{_WSMAN}"'
        f' xmlns:wsmv="{_WSMV}"'
        f' xmlns:rsp="{_RSP}">'
        f'<env:Header>'
        f'<wsa:To>{endpoint}</wsa:To>'
        f'<wsa:ReplyTo>'
        f'<wsa:Address env:mustUnderstand="true">{_ANON}</wsa:Address>'
        f'</wsa:ReplyTo>'
        f'<wsman:MaxEnvelopeSize env:mustUnderstand="true">153600</wsman:MaxEnvelopeSize>'
        f'<wsa:MessageID>{msg_id}</wsa:MessageID>'
        f'<wsman:Locale env:mustUnderstand="false" xml:lang="en-US"/>'
        f'<wsmv:DataLocale env:mustUnderstand="false" xml:lang="en-US"/>'
        f'<wsa:Action env:mustUnderstand="true">{action}</wsa:Action>'
        f'<wsman:SelectorSet>'
        f'<wsman:Selector Name="ShellId">{shell_id}</wsman:Selector>'
        f'</wsman:SelectorSet>'
        f'<wsman:OperationTimeout>{timeout}</wsman:OperationTimeout>'
        f'<wsman:ResourceURI env:mustUnderstand="true">{_RES_URI}</wsman:ResourceURI>'
        f'</env:Header>'
        f'<env:Body>{body_xml}</env:Body>'
        f'</env:Envelope>'
    )


def _receive_partial(protocol, shell_id: str, cmd_id: str) -> tuple[bytes, bytes, bool]:
    """发送 WSMan Receive（PT1S 超时），返回 (stdout, stderr, is_done)。超时时返回空数据。"""
    body = (
        f'<rsp:Receive xmlns:rsp="{_RSP}">'
        f'<rsp:DesiredStream CommandId="{cmd_id}">stdout stderr</rsp:DesiredStream>'
        f'</rsp:Receive>'
    )
    xml = _soap_envelope(protocol.endpoint, shell_id, _ACT_RECV, "PT1S", body)
    try:
        response = protocol.transport.send_message(xml)
    except Exception as exc:
        err = str(exc)
        if any(kw in err for kw in _TIMEOUT_KEYWORDS):
            return b"", b"", False
        raise
    return _parse_receive_response(response)


def _parse_receive_response(xml_str: str) -> tuple[bytes, bytes, bool]:
    root = ET.fromstring(xml_str)
    body = root.find(f"{{{_SOAP}}}Body")
    if body is None:
        return b"", b"", False
    recv = body.find(f"{{{_RSP}}}ReceiveResponse")
    if recv is None:
        return b"", b"", False

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    for stream in recv.findall(f"{{{_RSP}}}Stream"):
        name = stream.get("Name", "")
        text = (stream.text or "").strip()
        data = base64.b64decode(text) if text else b""
        if name == "stdout":
            stdout_chunks.append(data)
        elif name == "stderr":
            stderr_chunks.append(data)

    state_el = recv.find(f"{{{_RSP}}}CommandState")
    is_done = state_el is not None and state_el.get("State", "") == _STATE_DONE

    return b"".join(stdout_chunks), b"".join(stderr_chunks), is_done


def _send_signal(protocol, shell_id: str, cmd_id: str, code: str = _CTRL_C) -> None:
    """发送 WSMan Signal（ctrl_c）。"""
    body = (
        f'<rsp:Signal xmlns:rsp="{_RSP}" CommandId="{cmd_id}">'
        f'<rsp:Code>{code}</rsp:Code>'
        f'</rsp:Signal>'
    )
    xml = _soap_envelope(protocol.endpoint, shell_id, _ACT_SIG, "PT60S", body)
    try:
        protocol.transport.send_message(xml)
    except Exception:
        pass


def run_winrm_pty(session: "Session") -> None:
    """启动 WinRM 交互式 PTY 会话。"""
    from wmitool.terminal import RawTerminal

    protocol = session._winrm_protocol
    done = threading.Event()

    with RawTerminal():
        shell_id = protocol.open_shell()
        cmd_id: str | None = None
        try:
            cmd_id = protocol.run_command(shell_id, "cmd")

            def _output_loop() -> None:
                while not done.is_set():
                    try:
                        stdout, stderr, is_done = _receive_partial(protocol, shell_id, cmd_id)
                    except Exception:
                        done.set()
                        break
                    if stdout:
                        sys.stdout.buffer.write(stdout)
                        sys.stdout.buffer.flush()
                    if stderr:
                        sys.stderr.buffer.write(stderr)
                        sys.stderr.buffer.flush()
                    if is_done:
                        done.set()

            out_thread = threading.Thread(target=_output_loop, daemon=True)
            out_thread.start()

            stdin_fd = sys.stdin.fileno()
            try:
                while not done.is_set():
                    r, _, _ = select.select([stdin_fd], [], [], 0.2)
                    if not r:
                        continue
                    byte = os.read(stdin_fd, 1)
                    if not byte:
                        break
                    if byte == b"\x03":   # ^C
                        _send_signal(protocol, shell_id, cmd_id)
                    elif byte == b"\x04": # ^D EOF
                        break
                    else:
                        try:
                            protocol.send_input(shell_id, cmd_id, byte)
                        except Exception:
                            done.set()
                            break
            except KeyboardInterrupt:
                _send_signal(protocol, shell_id, cmd_id)
            finally:
                done.set()
                out_thread.join(timeout=3)

        finally:
            if cmd_id:
                try:
                    protocol.cleanup_command(shell_id, cmd_id)
                except Exception:
                    pass
            try:
                protocol.close_shell(shell_id)
            except Exception:
                pass
