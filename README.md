# wmitool

通过 WMI/DCOM 或 WinRM 协议连接远程 Windows 机器的 CLI 工具，提供交互式 Shell 和文件传输功能。目标机器无需安装任何额外软件。

## 安装

```bash
pip install .
```

## 使用

### 交互式 Shell（SSH 模式）

```bash
# DCOM（默认）
wmitool ssh 192.168.1.100 -u Administrator -p MyPass

# 域账户
wmitool ssh 192.168.1.100 -u "DOMAIN\user" -p MyPass

# WinRM
wmitool ssh 192.168.1.100 -u Administrator -p MyPass --protocol winrm

# WinRM + HTTPS
wmitool ssh 192.168.1.100 -u Administrator -p MyPass --protocol winrm --ssl
```

### 文件传输（SFTP 模式）

```bash
wmitool sftp 192.168.1.100 -u Administrator -p MyPass
```

SFTP 模式内置命令：

| 命令 | 说明 |
|------|------|
| `ls [路径]` | 列出远程目录 |
| `get <远程> [本地]` | 下载文件 |
| `put <本地> [远程]` | 上传文件 |
| `mkdir <路径>` | 创建远程目录 |
| `rm <路径>` | 删除远程文件 |
| `cd <路径>` | 切换远程目录 |
| `pwd` | 显示远程当前目录 |
| `lls [路径]` | 列出本地目录 |
| `lcd <路径>` | 切换本地目录 |
| `exit` / `quit` | 退出 |

## 选项

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-u / --user` | 必填 | 用户名，支持 `DOMAIN\user` 格式 |
| `-p / --password` | 必填 | 密码（允许空字符串） |
| `--protocol` | `dcom` | `dcom` 或 `winrm` |
| `--port` | 135 / 5985 | 随协议自动切换默认值 |
| `--ssl` | 否 | WinRM 模式下启用 HTTPS |
| `--smb-port` | 445 | SMB 文件传输端口（仅 sftp 模式） |
| `--verbose` | 否 | 显示详细调试信息 |

## 依赖

- [impacket](https://github.com/fortra/impacket) — DCOM/WMI 执行 + SMB 文件传输
- [pywinrm](https://github.com/diyan/pywinrm) — WinRM 协议
- [prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit) — 交互式输入与命令历史

## 注意事项

- Shell 模式基于轮询实现，不支持实时流输出（如 `ping -t`）
- SFTP 模式优先使用 SMB（445 端口），不可用时自动回退到 WMI+Base64 分块传输
