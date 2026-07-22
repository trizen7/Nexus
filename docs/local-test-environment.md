# 本地测试环境（无需 Docker）

Nexus 提供一套与正式部署完全隔离的本地测试环境，用于连接本机已运行的 Hermes API Server。该环境不会启动、停止或修改 Hermes，也不会在本机调用 Docker。

## 一次性搭建

在仓库根目录运行：

~~~bat
scripts\local-test.cmd setup
~~~

脚本会自动完成：

1. 在 **.local-test/venv/** 创建隔离的 Python 环境；
2. 使用 **uv pip compile** 与 **uv pip sync** 精确解析和同步 **gateway/requirements-dev.txt**；
3. 从 **%LOCALAPPDATA%\Hermes\config.yaml** 自动读取 Hermes API Server 地址与 Key；
4. 在 **.local-test/** 生成独立的 Nexus 测试账号、密码和 Session Secret；
5. 在 **http://127.0.0.1:18787** 后台启动 Nexus Gateway；
6. 验证 Nexus 健康检查、账号登录和 **/api/sessions** 到 Hermes 的认证代理。

Hermes Key 和测试密码不会出现在普通命令输出或 Gateway 日志中。需要人工登录管理页面时，必须显式运行：

~~~bat
scripts\local-test.cmd credentials
~~~

该命令会显示本机测试密码，请勿复制到 Issue、提交、截图或共享日志中。

## 生命周期命令

| 命令 | 行为 |
| --- | --- |
| **setup** | 首次创建或修复环境，启动 Gateway 并执行冒烟测试 |
| **start** | 启动环境；发现源码、依赖或 Hermes 配置变化时自动重启 |
| **stop** | 只停止 .local-test/process.json 记录的 Nexus Gateway，不触碰 Hermes |
| **restart** | 重新读取 Hermes 配置并重启 Gateway |
| **status** | 检查进程、Nexus /health 和 Hermes /health，不显示密钥 |
| **smoke** | 更新到当前环境状态，并验证健康检查、登录和 Hermes 会话代理 |
| **upgrade** | 强制重新解析/同步依赖、同步 Hermes 配置、重启并执行冒烟测试 |
| **verify** | 执行冒烟测试、Gateway pytest、网页契约、Android 单测/Lint/Debug 构建；不调用 Docker |
| **reset** | 清除测试数据、账号和日志，保留虚拟环境；不会删除 Hermes 数据 |
| **credentials** | 显式显示本地测试地址、账号和密码 |

PowerShell 入口也可使用：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-test.ps1 status
~~~

## 随迭代升级约定

每次需求或 Bug 修复完成后，统一执行：

~~~bat
scripts\local-test.cmd upgrade
scripts\local-test.cmd verify
~~~

**upgrade** 是本地环境的升级入口。它会重新读取当前依赖文件和 Hermes 配置，记录当前 Git commit、源码摘要、依赖摘要及 Hermes/Nexus 版本，然后重启当前代码并完成端到端冒烟测试。

**start** 也会比较运行中进程记录的源码、依赖和配置摘要；如果当前工作区已发生变化，会自动重启，因此本地 Gateway 不会长期停留在旧迭代。

**verify** 是非 Docker 的完整本地门禁。Gateway 测试中包含对 Dockerfile/Compose 文件的静态契约检查，但不会执行 docker build、docker compose 或启动容器。

## 数据隔离

所有运行数据都位于被 Git 忽略的 **.local-test/**：

~~~text
.local-test/
├─ venv/                    # 隔离 Python 环境
├─ data/
│  ├─ account.json          # scrypt 密码哈希
│  ├─ config.json           # Hermes Key 与 Session Secret
│  ├─ media/
│  └─ run_status.json
├─ logs/gateway.log
├─ access.json              # 本地测试账号和明文测试密码
├─ dependency-state.json
├─ requirements.lock.txt
├─ process.json
└─ runtime.json
~~~

**.local-test/** 不得强制加入 Git。脚本的 status、start、upgrade 和 verify 不会输出 access.json 或 config.json 中的秘密。

## Hermes 探测与覆盖

默认配置文件：

~~~text
%LOCALAPPDATA%\Hermes\config.yaml
~~~

如安装位置不同，可在当前终端覆盖：

~~~powershell
$env:NEXUS_LOCAL_HERMES_CONFIG = "D:\path\to\config.yaml"
~~~

也可以临时显式设置连接信息：

~~~powershell
$env:NEXUS_LOCAL_HERMES_URL = "http://127.0.0.1:8642"
$env:NEXUS_LOCAL_HERMES_TOKEN = "仅在当前终端中设置"
~~~

测试 Gateway 的监听地址可通过以下变量调整：

~~~powershell
$env:NEXUS_LOCAL_TEST_HOST = "127.0.0.1"
$env:NEXUS_LOCAL_TEST_PORT = "18787"
~~~

这些覆盖值不应写入仓库文件。

## 排查

~~~bat
scripts\local-test.cmd status
scripts\local-test.cmd smoke
~~~

Gateway 日志位于 **.local-test/logs/gateway.log**。日志可用于本机排障，但其中仍可能包含请求路径和状态信息，不应直接上传；分享前必须脱敏。

如果测试数据损坏，可运行：

~~~bat
scripts\local-test.cmd reset
scripts\local-test.cmd setup
~~~

**reset** 仅处理仓库内 **.local-test/** 的子目录，不停止或清理 Hermes。
