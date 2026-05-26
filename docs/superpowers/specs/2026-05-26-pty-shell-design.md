# PTY Shell 设计文档

**日期：** 2026-05-26  
**目标：** 为 wmitool 实现类 SSH 的实时交互式 Shell，支持流式回显、^C/^D/^Z、窗口大小调整。

---

## 背景与约束

- 目标机器：Windows，无 SSH Server
- 客户端：Linux、Windows
- 现有协议：DCOM/WMI（port 135）、WinRM（port 5985/5986）
- PowerShell 可能被机房管理程序杀死，方案须有 cmd 回退
- 不引入 Go 二进制或任何需要持久化到目标的文件

---

## 核心策略

**PTY 交互层统一走 WinRM**（原生支持流式 stdin/stdout/signal），DCOM 作为启动 WinRM 的引导通道。

```
DCOM 连接
  → bootstrap_winrm()   （正向连接，开启 5985）
  → WinRM PTY 会话      （流式交互）
```

WinRM 直连时跳过 bootstrap 步骤。

---

## 一、WinRM Bootstrap（`session.py`）

新增方法 `Session.bootstrap_winrm() -> bool`，通过已有 DCOM 连接开启 WinRM 监听，三步递进：

### 步骤 1：PowerShell（优先）

```
powershell -Command "Enable-PSRemoting -Force -SkipNetworkProfileCheck"
```

一条命令完成：启动服务、配置监听器、放行防火墙。执行后 probe TCP 5985，通则返回 `True`。

### 步骤 2：纯 cmd（PowerShell 被杀时回退）

```
sc config WinRM start= auto
sc start WinRM
winrm quickconfig -quiet
netsh advfirewall firewall add rule name="WinRM-wmitool" dir=in action=allow protocol=TCP localport=5985
```

执行后再 probe TCP 5985，通则返回 `True`。

### 步骤 3：失败

返回 `False`，调用方降级到现有 DCOM batch shell，并向用户打印提示。

**probe 实现：** `socket.create_connection((host, 5985), timeout=1)`，重试间隔 1s，最长等待 15s。

---

## 二、本地终端层（`terminal.py`）

```python
class RawTerminal:
    """上下文管理器：进入时设 raw mode，退出时恢复（含异常路径）"""

    def __enter__(self) -> RawTerminal
    def __exit__(self, *args) -> None
    def get_size(self) -> tuple[int, int]   # (cols, rows)，读 TIOCGWINSZ
```

- 使用 `tty.setraw(sys.stdin.fileno())` 进入 raw mode
- 注册 `SIGWINCH` handler，收到信号后将新尺寸写入队列供 PTY 线程消费
- 仅在 `sys.stdin.isatty()` 时启用，否则透传

---

## 三、WinRM PTY（`winrm_pty.py`）

### 3.1 控制字符映射

| 按键 | 字节 | 处理 |
|------|------|------|
| ^C | 0x03 | `_send_signal(shell_id, cmd_id, 'ctrl_c')` |
| ^D | 0x04 | 关闭 stdin 流，等待命令退出 |
| ^Z | 0x1A | 直接写入 `send_input`（cmd 内 EOF 语义） |

### 3.2 流式输出：`_receive_partial`

pywinrm 的 `get_command_output` 阻塞到命令结束，不可用。改为用其底层 transport 发送自定义 WSMan Receive envelope：

```xml
<wsman:Receive>
  <wsman:DesiredStream>stdout stderr</wsman:DesiredStream>
</wsman:Receive>
```

附加 HTTP header `wsman:OperationTimeout: PT1S`，每次调用返回当前可用数据块（可为空），循环直到收到 `CommandState=Done`。

### 3.3 信号：`_send_signal`

构造 WSMan Signal envelope，Code 为：
```
http://schemas.microsoft.com/wbem/wsman/1/windows/shell/signal/ctrl_c
```

### 3.4 线程结构

```
with RawTerminal() as term:
    shell_id = protocol.open_shell()
    cmd_id   = protocol.run_command(shell_id, 'cmd')

    Thread（输出）:
        while True:
            chunks = _receive_partial(shell_id, cmd_id)  # timeout PT1S
            write chunks to sys.stdout.buffer
            if done: break

    Main thread（输入）:
        while True:
            byte = sys.stdin.buffer.read(1)
            if byte == b'\x03': _send_signal(ctrl_c)
            elif byte == b'\x04': break
            else: protocol.send_input(shell_id, cmd_id, byte)

    SIGWINCH: 无法通知 WinRM 调整窗口大小，忽略（WinRM 协议限制）

    cleanup_command(); close_shell()
```

---

## 四、统一入口（`pty_shell.py`）

```python
def run_pty_shell(session: Session) -> None:
    if not sys.stdin.isatty():
        # 非 tty 场景（管道、脚本）降级到 batch shell
        run_shell(session)
        return

    if session.protocol == 'winrm':
        _run_winrm_pty(session)
        return

    # DCOM 路径：bootstrap WinRM
    print(f"[*] 尝试通过 DCOM 启用 WinRM ({session.host}:5985)...")
    if session.bootstrap_winrm():
        winrm_session = Session(
            host=session.host,
            user=session.user,
            password=session.password,
            protocol='winrm',
            port=5985,
        )
        winrm_session.connect()
        _run_winrm_pty(winrm_session)
    else:
        print("[!] WinRM 不可达，降级到 DCOM batch shell（无实时回显）")
        run_shell(session)
```

---

## 五、DCOM execute 退出码修复（`session.py`）

现有 `_execute_dcom` 硬返回 `rc=0`，改为将 `%ERRORLEVEL%` 写入独立临时文件：

```
cmd /c "<command>" > out_<uuid>.tmp 2>&1
echo %ERRORLEVEL% > exit_<uuid>.tmp
```

通过 SMB 读取两个文件，`exit.tmp` 内容解析为整数作为 `rc` 返回。两个临时文件读取后均删除。

---

## 六、`cli.py` 改动

`cmd_ssh` 改为调用 `run_pty_shell(session)`，`shell.py` 的 `run_shell` 保留为 batch fallback，不再作为主路径。

---

## 七、文件清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `wmitool/terminal.py` | 新增 | raw tty + SIGWINCH |
| `wmitool/pty_shell.py` | 新增 | 路径调度入口 |
| `wmitool/winrm_pty.py` | 新增 | 流式 WinRM PTY |
| `wmitool/session.py` | 修改 | `bootstrap_winrm()` + DCOM 退出码 |
| `wmitool/shell.py` | 修改 | 降级为 batch fallback，不再主路径 |
| `wmitool/cli.py` | 修改 | `cmd_ssh` 调用 `run_pty_shell` |
| `pyproject.toml` | 不变 | 无新依赖 |

---

## 八、依赖

现有依赖已满足：
- `pywinrm`：WinRM transport + Protocol
- `impacket`：DCOM connect + SMB（bootstrap_winrm 的 execute 走现有路径）
- `prompt_toolkit`：batch fallback shell 保留使用
- 无新增第三方包
