# 本地测试环境（无需 Docker）

Nexus 提供两套与正式部署隔离的非 Docker 测试环境，均连接已经运行的 Hermes API Server：

- `.local-test/`：开发回归环境，直接运行当前源码；
- `成品/本地测试环境/`：用户验收环境，只运行已经构建的 Gateway ZIP，不读取源码。

两套环境默认都使用 **HTTP `18787`**，不能同时启动。旧 HTTPS `18788` 必须关闭。这些脚本只连接已经由用户运行的原版 Hermes API；不会安装、更新、回滚、卸载、启动、停止、重启或清理 Hermes，也不会写入任何 Hermes 文件。

## Hermes 原版只读边界

- Nexus 只能通过原版 Hermes HTTP API 工作，不依赖 fork、补丁、内部数据库或私有文件布局；
- 开发测试环境可以只读解析已有 Hermes 配置来取得 API 地址与 Key，也可以使用显式环境变量；
- 读取到的连接信息只会复制到 `.local-test/data/` 或成品环境 `data/` 等 Nexus 自有目录，绝不回写 Hermes；
- reset、upgrade、依赖同步、成品部署和进程管理只处理 Nexus 自有目录与 Nexus Gateway 进程；
- 聊天、会话、模型查询和定时任务可通过公开 API 正常调用；Hermes 自行持久化 API 状态不代表 Nexus 直接修改 Hermes 文件。

## HTTP 源站与反向代理原则

- Gateway 只提供 HTTP 源站，不生成或管理证书。
- 可信局域网设备可直接访问 `http://电脑局域网IP:18787`。
- 不要把 HTTP 源站端口直接暴露到公网。
- 外网使用 Nginx、Caddy 等反向代理提供受系统信任的 `https://域名`；证书续期、HTTP→HTTPS 跳转、HSTS 和公网访问控制由反向代理负责。
- 反向代理应转发 `Host` 与 `X-Forwarded-Proto`、关闭响应缓冲并设置较长读取超时，以支持 SSE/流式回答。
- Gateway 不盲目信任 `X-Forwarded-*`，源站仍必须通过监听地址、防火墙或网络拓扑隔离。

Android 接受显式 HTTP 或 HTTPS 地址，不阻止公网 HTTP，也不要求安装证书或显示 HTTPS 强制提示。遗漏协议时只补全 `http://`，不猜测端口，也不改写已保存地址。公网部署仍建议由反向代理提供受信任的 HTTPS。

## 独立成品验收环境

控制文件模板位于：

```text
scripts/product-test-environment/
```

同步到被 Git 忽略的独立运行目录：

```bat
scripts\sync-product-test-environment.cmd
```

同步命令只覆盖 `manage.ps1`、快捷方式和使用说明，并删除已经废弃的本机 CA 安装快捷方式；不会修改 `app/`、`data/`、`venv/`、`logs/` 或 `state/`。因此可以随迭代升级控制逻辑，同时保留管理员账号、密码哈希、Nexus 保存的 Hermes 地址与 Key 副本、Session Secret、媒体和历史 TLS 数据。

运行目录中的入口：

| 文件 | 行为 |
| --- | --- |
| `01-打开初始化页面.cmd` | 启动环境并打开 `http://127.0.0.1:18787` |
| `02-查看状态.cmd` | 显示 HTTP 地址以及 18787/18788 状态 |
| `03-停止测试环境.cmd` | 只停止该目录安全记录的 Gateway 进程 |
| `04-清空并重新开始.cmd` | 显式清除整个测试数据目录；普通升级不得使用 |
| `05-升级到最新成品.cmd` | 无损递归部署成品目录中版本号最高的 `Nexus-Gateway-*.zip`，归档已有非空 stdout/stderr 日志后重启 |
| `06-查看反向代理说明.cmd` | 打开本环境使用说明 |

端口约定：

- `http://127.0.0.1:18787`：本机网页与 API；
- `http://局域网IP:18787`：手机和其他可信局域网设备；
- TCP `18788`：旧 HTTPS 端口，必须关闭；
- Windows 防火墙只允许 `LocalSubnet` 访问 TCP `18787`。

`manage.ps1 upgrade` 会安全停止本环境记录的旧 18788 进程，只替换 `app/` 并同步依赖，不执行 reset。整个 `data/`（包括历史 `data/tls/`）会保留。`manage.ps1 reset` 才会删除账号、配置、媒体、日志和整个 `data/`。

## 开发回归环境：一次性搭建

在仓库根目录运行：

```bat
scripts\local-test.cmd setup
```

脚本会自动：

1. 在 `.local-test/venv/` 创建隔离 Python 环境；
2. 使用独立系统 Python 自带的 `venv + pip` 在 `.local-test/` 内重建并安装固定版本依赖，缓存也限制在 Nexus 自有目录；
3. 以只读方式从本机已有 Hermes 配置获取 API Server 地址与 Key，绝不回写；
4. 在 `.local-test/` 生成独立 Nexus 测试账号、密码和 Session Secret；
5. 在 `http://127.0.0.1:18787` 启动当前源码；
6. 通过禁用环境代理的本机直连请求执行健康检查、登录和 Hermes 代理冒烟测试。

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
| `restart` | 只读重新获取 Hermes 连接信息，并仅重启 Nexus Gateway |
| `status` | 检查进程、Nexus `/health` 和 Hermes `/health`，不显示密钥 |
| `smoke` | 验证 HTTP 健康检查、账号登录和 Hermes 会话代理 |
| `upgrade` | 强制同步 Nexus 依赖与上游连接信息副本，仅重启 Gateway 并执行冒烟测试 |
| `verify` | 执行冒烟、Gateway pytest、网页契约、Android 单测/Lint/Debug 构建；不调用 Docker |
| `reset` | 清除 `.local-test` 数据、账号和日志，保留虚拟环境；不删除 Hermes 数据 |
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

成品升级前后只记录以下数据的存在状态、数量或哈希，不显示内容：

- `data/account.json`；
- `data/config.json`；
- `data/media/`；
- 历史 `data/tls/`（如果存在）。

普通升级后账号、Nexus 保存的 Hermes 上游连接配置副本、媒体和历史 TLS 文件哈希必须保持不变；日志不清理，每次启动前将已有非空 `gateway.stdout.log` / `gateway.stderr.log` 移入同目录带 UTC 时间戳的归档文件，再验证 `18787` 正在监听、`18788` 未监听、`process.json` 记录的 PID 与实际监听进程一致。

## 0.1.0 开源正式版成品

正式成品由发布脚本生成到被 Git 忽略的 `成品/v0.1.0/`，包含官方签名 Release APK、Gateway ZIP 与 `SHA256SUMS.txt`。第三方许可声明保留在 Gateway ZIP 中。独立验收环境仍只部署 Gateway ZIP，不读取源码、不运行 Docker，也不接触签名私钥。

手机验收应安装 `Nexus-Android-0.1.0-release.apk`；升级已有官方版本前应确认 APK 签名来自同一官方发布密钥。

## 0.0.15 成品

当前成品文件：

- `成品/Nexus-Android-0.0.15-debug.apk`；
- `成品/Nexus-Gateway-0.0.15.zip`；
- `成品/SHA256SUMS.txt`。

Debug APK 不内嵌本地 CA。App 可连接 HTTP 或由 Android 系统信任链验证的 HTTPS；公网 HTTP 不再由客户端拦截，但仅建议用于明确可接受明文传输风险的环境。

Android 聊天输入使用原生焦点流程，连续输入只维持一个尾随草稿保存任务；登录页和聊天页直接采用系统预先给出的输入法最终高度，不再跟随键盘动画逐帧重新测量页面，消息列表也不再等待 300ms 后才定位。Android 聊天请求会携带手机端能力声明，Gateway 仅通过原版 Hermes HTTP API 注入本轮移动端提示；Hermes 不应把主机本地路径、桌面拖拽或电脑快捷键当作手机附件交付方式。人物列表只接受 Hermes 明确标记为 persona 的条目，主模型名称不会再被误显示为人物；未选择人物时使用 `Hermes 默认（default）`。QQ、微信等其他渠道正在处理消息时，App 会以只读方式显示“思考中”，但不会提供停止按钮。 Android 发送成功建立 SSE 后若因锁屏或切网失去实时连接，会保留已提交消息、清空其持久化草稿且禁止自动重试聊天 POST，再通过 Gateway 运行状态续接；唤醒时的被动刷新不会把短暂网络恢复延迟误报为服务器不可达。

## 推理深度

Android 中的人物模型作为全局设定；实际调用模型和推理深度按对话单独保存。推理深度选项为默认、`none`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`、`ultra`。

Nexus 只负责 UI、持久化、白名单校验和 `reasoning_effort` 字段透传。Hermes 和所选调用模型是否支持、是否实际采用该值，由它们自身实现决定。

## 安全边界

- `.local-test/`、`成品/` 与运行时 `data/` 均被 Git 忽略；
- 普通命令不会输出 Hermes Key、Token、密码、Session Secret 或 Bootstrap Token；
- 不提交 `*.key`、历史 CA、反向代理证书私钥、日志、截图或运行数据；
- reset/upgrade 只处理 Nexus 自有目录，不修改任何 Hermes 文件，也不管理 Hermes 进程；
- 测试环境不调用 Docker；
- Windows 防火墙仅允许 `LocalSubnet` 访问 TCP `18787`；
- 公网只开放反向代理的 HTTPS 入口。
