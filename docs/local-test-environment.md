# 本地测试环境（无需 Docker）

Nexus 提供两套与正式部署隔离的非 Docker 测试环境，均连接已经运行的 Hermes API Server：

- `.local-test/`：开发回归环境，直接运行当前源码；
- `成品/本地测试环境/`：用户验收环境，只运行已经构建的 Gateway ZIP，不读取源码。

两套环境默认都使用 **HTTPS `18788`**，不能同时启动。Gateway 不再监听 HTTP `18787`。这些脚本不会启动、停止或清理 Hermes，Hermes 配置、会话和定时任务也不属于 Nexus reset/upgrade 的处理范围。

## HTTPS 与证书原则

HTTPS 协议本身必须使用服务器证书，但当前测试不要求购买证书或让手机手动安装证书：

1. Gateway 首次启动会在各自环境的 `data/tls/` 中自动生成临时 CA 和服务器证书；
2. CA 会持久保存；局域网 IP 或 SAN 变化时只重签服务器证书，不替换 CA；
3. `Nexus-Android-0.0.6-debug.apk` 使用构建时指定的测试环境 CA，因此连接该测试环境时手机无需安装 CA；
4. Release APK 只信任 Android 系统 CA；
5. 以后取得正式证书后，可在网页“系统状态 → HTTPS 证书”上传完整 PEM 证书链和未加密 PEM 私钥，Gateway 会校验、备份并热切换，失败时回滚；
6. 正式证书是否被 Release APK 或普通浏览器直接信任，仍取决于签发 CA 是否在系统信任库中，以及访问域名/IP 是否包含在证书 SAN 中。

Windows 浏览器若要消除临时 CA 警告，可由用户主动运行 `06-安装本机HTTPS证书.cmd`。其他非 App 设备应通过 U 盘或受信任文件共享取得 `data/tls/ca.crt`，不要通过明文 HTTP 下载或传输。

## 独立成品验收环境

控制文件模板位于：

```text
scripts/product-test-environment/
```

同步到被 Git 忽略的独立运行目录：

```bat
scripts\sync-product-test-environment.cmd
```

同步命令只覆盖 `manage.ps1`、快捷方式和使用说明，不修改 `app/`、`data/`、`venv/`、`logs/` 或 `state/`。因此可以随迭代升级控制逻辑，同时保留管理员账号、密码哈希、Hermes 地址与 Key、Session Secret、媒体和 TLS 状态。

运行目录中的入口：

| 文件 | 行为 |
| --- | --- |
| `01-打开初始化页面.cmd` | 启动环境并打开 `https://127.0.0.1:18788` |
| `02-查看状态.cmd` | 显示 HTTPS 地址、18788/18787 状态、CA 文件与 SHA-256 指纹 |
| `03-停止测试环境.cmd` | 只停止该目录拥有的 Gateway 进程 |
| `04-清空并重新开始.cmd` | 显式清除测试数据和本地 CA；普通升级不得使用 |
| `05-升级到最新成品.cmd` | 无损部署父目录最新 `Nexus-Gateway-*.zip` 并重启 |
| `06-安装本机HTTPS证书.cmd` | 用户主动把临时 CA 加入当前 Windows 用户信任库 |

端口约定：

- `https://127.0.0.1:18788`：本机网页与 API；
- `https://局域网IP:18788`：手机和其他局域网设备的唯一入口；
- TCP `18787`：必须关闭，管理脚本不会创建 HTTP 监听或防火墙放行规则。

`manage.ps1 upgrade` 只替换 `app/` 并同步依赖，不执行 reset，整个 `data/tls/` 会保留。`manage.ps1 reset` 才会删除账号、配置、媒体、日志和 CA。重置会生成新 CA，旧 Debug APK 内嵌的 CA 将不再匹配，需重新构建 Debug APK。

## 开发回归环境：一次性搭建

在仓库根目录运行：

```bat
scripts\local-test.cmd setup
```

脚本会自动：

1. 在 `.local-test/venv/` 创建隔离 Python 环境；
2. 使用 `uv pip compile` 与 `uv pip sync` 同步 `gateway/requirements-dev.txt`；
3. 从本机 Hermes 配置自动读取 API Server 地址与 Key；
4. 在 `.local-test/` 生成独立 Nexus 测试账号、密码和 Session Secret；
5. 在 `https://127.0.0.1:18788` 启动当前源码；
6. 通过只供管理脚本使用的未验证 TLS context 执行本机健康检查、登录和 Hermes 代理冒烟测试。

未验证 TLS context 只存在于本地管理脚本，不会开启 HTTP，也不会降低 Android App、浏览器或 Gateway 对外连接的证书规则。

Hermes Key 和测试密码不会输出到普通状态、日志或提交中。需要人工登录时必须显式运行：

```bat
scripts\local-test.cmd credentials
```

该命令会显示本机测试密码，请勿复制到 Issue、提交、截图或共享日志。

## 生命周期命令

| 命令 | 行为 |
| --- | --- |
| `setup` | 首次创建或修复环境，启动 Gateway 并执行冒烟测试 |
| `start` | 启动环境；发现源码、依赖或配置变化时自动重启 |
| `stop` | 只停止 `.local-test/process.json` 记录的 Nexus Gateway |
| `restart` | 重新读取 Hermes 配置并重启 Gateway |
| `status` | 检查进程、Nexus `/health` 和 Hermes `/health`，不显示密钥 |
| `smoke` | 验证 HTTPS 健康检查、账号登录和 Hermes 会话代理 |
| `upgrade` | 强制同步依赖和 Hermes 配置，重启并执行冒烟测试 |
| `verify` | 执行冒烟、Gateway pytest、网页契约、Android 单测/Lint/Debug 构建；不调用 Docker |
| `reset` | 清除 `.local-test` 数据、TLS 状态、账号和日志，保留虚拟环境；不删除 Hermes 数据 |
| `credentials` | 显式显示本地测试地址、账号和密码 |

常用命令：

```bat
scripts\local-test.cmd setup
scripts\local-test.cmd smoke
scripts\local-test.cmd upgrade
scripts\local-test.cmd verify
scripts\local-test.cmd reset
```

PowerShell 入口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-test.ps1 status
```

## 随迭代升级约定

每次需求或 Bug 修复完成后，开发环境统一执行：

```bat
scripts\local-test.cmd upgrade
scripts\local-test.cmd verify
```

成品验收环境统一执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\sync-product-test-environment.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File ".\成品\本地测试环境\manage.ps1" upgrade
```

成品升级前后应只记录以下数据的存在状态、数量或哈希，不显示内容：

- `data/account.json`；
- `data/config.json`；
- `data/media/`；
- `data/tls/ca.crt`。

普通升级后账号、Hermes 配置、媒体和 CA 指纹必须保持不变，同时验证 `18788` 正在监听、`18787` 未监听。

## Android Debug CA 构建

构建当前验收环境专用 Debug APK：

```powershell
$env:NEXUS_DEBUG_CA_FILE = "$PWD\成品\本地测试环境\data\tls\ca.crt"
Set-Location .\android
.\gradlew.bat assembleDebug --no-daemon
```

构建脚本只读取公开 CA 证书并拒绝包含 `PRIVATE KEY` 的输入。不要把真实测试 CA、任何私钥或 `data/` 提交到 Git。

## 推理深度

Android 可独立选择人物模型、实际调用模型和推理深度。推理深度选项为默认、`none`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`、`ultra`。

Nexus 只负责 UI、持久化、白名单校验和 `reasoning_effort` 字段透传。Hermes 和所选调用模型是否支持、是否实际采用该值，由它们自身实现决定。

## 安全边界

- `.local-test/`、`成品/` 与运行时 `data/` 均被 Git 忽略；
- 普通命令不会输出 Hermes Key、Token、密码、Session Secret 或 Bootstrap Token；
- 不提交 `*.key`、真实测试 CA、正式证书私钥、日志、截图或运行数据；
- reset/upgrade 只处理 Nexus 自己的目录，不删除或修改 Hermes 数据；
- 测试环境不调用 Docker；
- Windows 防火墙仅允许 `LocalSubnet` 访问 TCP `18788`。
