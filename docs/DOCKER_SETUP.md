# Docker 配置与排障

[返回首页](../README.md) · [安装与验收](../README.md#检查安装)

AgentLab 使用 Docker 运行任务代码。请先确保启动应用的普通用户能够连接 Docker 服务；Docker 不可用时，应用会禁用任务执行，不会降级到宿主机运行。

## 检查连接

在准备启动应用的同一个终端执行：

```bash
command -v docker
docker --version
docker context show
docker version
docker info
```

`docker --version` 只证明 CLI 可用。`docker version` 应包含 Server 信息，`docker info` 应成功返回服务信息。

## Windows WSL2

使用 Docker Desktop 时：

1. 启动 Docker Desktop，等待引擎就绪，并使用 Linux 容器。
2. 在设置中启用 WSL2 引擎，并为当前 Linux 发行版打开 WSL Integration。
3. 重新打开该发行版的终端，再执行上述连接检查。
4. 从检查成功的终端启动应用。

若使用 VS Code WSL 或其他远程终端，必要时重新连接 WSL；旧终端服务可能仍保留原权限。已经使用 Desktop 集成时，无需为本项目再安装另一套 Docker Engine。

## Linux 与 macOS

Linux 可使用已有 Docker Engine 或 Docker Desktop；macOS 使用 Docker Desktop。先启动对应服务，再检查当前 context 是否指向预期环境。

采用 systemd 管理的 Linux Docker Engine，可以检查服务：

```bash
systemctl is-active docker
```

如果服务未启动且当前用户有管理权限，再执行：

```bash
sudo systemctl start docker
```

此命令适用于本机 Engine，不用于启动 Docker Desktop 的 WSL 集成服务。

## 权限不足

遇到 `permission denied` 时，先确认连接地址。如果使用本地 `/var/run/docker.sock`，检查当前进程、账户组与 socket 实际权限：

```bash
id
id "$USER"
getent group docker
readlink -f /var/run/docker.sock
stat -Lc '%U %G %a %n' /var/run/docker.sock
```

仅在 socket 属于 `docker` 组且允许组访问、账户尚未加入该组时，由有管理权限的用户执行：

```bash
sudo usermod -aG docker "$USER"
```

`-aG` 会保留已有附加组。Docker 组拥有很高的宿主权限，应由本机管理员决定授权。

如果账户已在组内，但当前 `id` 尚未显示该组，只需刷新登录会话，不要重复修改组配置。保存工作并停止应用，重新登录终端或连接 WSL，再确认普通用户的 `docker info` 成功，随后重启后端。

一次 `newgrp` 或 `sg` 只影响对应 shell 或子进程，不能刷新已经运行的编辑器、终端服务和后端。不要将 socket 改为全员可写，也不要使用 sudo 启动整套应用。

## 构建与检查任务镜像

在项目根目录执行：

```bash
docker build -t agentlab-runner:0.1 -f backend/Dockerfile .
.venv/bin/python scripts/docker_check.py
```

运行镜像标签与应用版本独立，V0.5 仍使用默认标签 `agentlab-runner:0.1`。如果配置了 `AGENTLAB_IMAGE`，构建和诊断命令需使用相同配置；诊断脚本不会自行加载 `.env`。

生成实例可能绑定已经验证的镜像 ID。不要仅为清理空间而删除仍被历史任务引用的镜像；重建同名标签并不保证与历史 ID 相同。

## 常见诊断结果

| 状态 | 含义 | 处理 |
| --- | --- | --- |
| `cli_missing` | CLI 不存在或不在 PATH | 检查安装、终端 PATH 或 WSL 集成 |
| `daemon_unreachable` | 无法连接服务 | 检查服务、context 与连接地址；不一定是服务未启动 |
| `permission_denied` | 当前进程无访问权限，也可能受执行沙箱限制 | 核对组与 socket 权限；比较普通终端和工具环境 |
| `image_missing` | 服务已连通，任务镜像不存在 | 在同一 Docker context 构建正确镜像 |
| `check_timeout` | 环境诊断超时 | 检查 Docker 是否响应及本机资源 |
| `daemon_error`、`image_error` | 服务或镜像检查失败 | 查看终端中的原始 Docker 错误，处理后重新检查 |

若普通终端能连接、只有受限工具环境不能连接，应处理工具的执行授权，不能通过扩大 socket 权限绕过。

## 清理执行资源

确认没有训练或生成正在执行，再运行：

```bash
./scripts/clean.sh
```

该脚本删除带 `agentlab=true` 标签的容器，可能包含尚在运行的项目容器；它保留训练数据与镜像。完整安装验证见[安装与验收](../README.md#检查安装)。
