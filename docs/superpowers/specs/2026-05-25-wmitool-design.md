# wmitool 设计规格

**日期：** 2026-05-25  
**状态：** 已审批

---

## 概述

`wmitool` 是一个 Python CLI 工具，通过 WMI/DCOM 或 WinRM 协议连接远程 Windows 机器，提供类似 SSH 的交互式 Shell 和类似 SFTP 的文件传输功能。目标机器无需安装任何额外软件。

---

## 命令接口

```bash
# SSH 模式（交互式 Shell）
wmitool ssh <host> -u <user> -p <pass> [选项]

# SFTP 模式（文件传输）
wmitool sftp <host> -u <user> -p <pass> [选项]
```

### 通用选项

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-u / --user` | 必填 | 用户名，支持 `DOMAIN\user` 格式 |
| `-p / --password` | 必填 | 密码，允许空字符串 `""` |
| `--protocol` | `dcom` | `dcom`（传统 WMI）或 `winrm`（新版） |
| `--port` | 135 / 5985 | WMI/WinRM 端口，随协议自动切换默认值 |
| `--ssl` | 否 | WinRM 模式下启用 HTTPS（默认端口 5986） |

### SFTP 专属选项

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--smb-port` | 445 | SMB 文件传输端口 |

---

## 架构

```
wmitool/
├── wmitool/
│   ├── __init__.py
│   ├── __main__.py       # python -m wmitool 入口
│   ├── cli.py            # argparse CLI 定义与路由
│   ├── session.py        # 连接管理（DCOM/WinRM + SMB）
│   ├── shell.py          # 交互式 Shell 实现
│   └── sftp.py           # 文件传输实现
├── pyproject.toml
└── README.md
```

### 核心依赖

| 库 | 用途 |
|----|------|
| `impacket` | DCOM 协议的 WMI 执行 + SMB 文件传输 |
| `pywinrm` | WinRM 协议的命令执行 |
| `prompt_toolkit` | 交互式输入、命令历史、Tab 补全 |

---

## 协议模式

### DCOM 模式（`--protocol dcom`，默认）

- 使用 impacket `dcerpc.v5.dcom.wmi`，通过端口 135 建立 DCOM 连接
- 命令执行采用 `wmiexec` 方案：
  1. 通过 `Win32_Process.Create()` 执行 `cmd.exe /Q /c <命令> > %TEMP%\__out_<uuid>.tmp 2>&1`
  2. 通过 SMB 读取输出文件内容
  3. 删除临时文件
  4. 维护当前工作目录状态，每次命令前追加 `cd /d <cwd> &&`
- 空密码：impacket 原生支持

### WinRM 模式（`--protocol winrm`）

- 使用 `pywinrm`，HTTP 默认端口 5985，HTTPS（`--ssl`）默认端口 5986
- 命令执行通过 WS-Management 协议直接获取 stdout/stderr
- 空密码：连接时传入空字符串凭据

---

## Shell 模式

**体验：**
- `prompt_toolkit` 提供命令历史（上下箭头）和行编辑
- 提示符格式：`[user@host] C:\Users\Administrator> `
- 每条命令执行后更新当前路径（解析 `cd` 命令或执行后查询 `%CD%`）
- `exit` / `quit` / Ctrl+D 退出

**限制：** 基于轮询的伪交互，不支持实时流输出（如 `ping -t`）；长时间运行的命令需等待完成才显示结果。

---

## SFTP 模式

### 文件传输策略

连接建立时自动探测 SMB 445 端口（可自定义）：
- **SMB 可用**：通过 impacket `SMBConnection` 传输，适合大文件
- **SMB 不可用**：回退到 WMI+Base64，将文件分块（每块 ≤ 250KB）编码后通过 WMI 写入

### 支持的命令

| 命令 | 说明 |
|------|------|
| `ls [路径]` | 列出远程目录（默认当前目录） |
| `get <远程> [本地]` | 下载文件到本地 |
| `put <本地> [远程]` | 上传文件到远程 |
| `mkdir <路径>` | 在远程创建目录 |
| `rm <路径>` | 删除远程文件 |
| `cd <路径>` | 切换远程当前目录 |
| `pwd` | 显示远程当前目录 |
| `lls [路径]` | 列出本地目录 |
| `lcd <路径>` | 切换本地当前目录 |
| `exit` / `quit` | 退出 |

---

## 错误处理

- 连接失败（认证错误、端口不通、目标不可达）：输出明确错误信息并退出，退出码非零
- SMB 探测失败：静默回退到 WMI+Base64，不打印警告（除非 `--verbose`）
- 空密码：允许传入，工具不做拦截；若目标拒绝则显示认证失败信息

---

## 打包

通过 `pyproject.toml` 定义，安装后提供 `wmitool` 命令：

```toml
[project.scripts]
wmitool = "wmitool.cli:main"
```
