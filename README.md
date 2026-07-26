# Nexus

Nexus 是一个面向 Hermes Agent 的社区移动客户端与轻量移动网关。

项目目标是让 Hermes 始终保持原版并由用户独立维护，同时把移动端所需的登录、会话、附件、缓存、下载、通知和断线续接能力放在客户端与移动网关中。Nexus 不修改、更新或管理任何 Hermes 文件。

当前版本：0.1.2

## 组成

- `android/`：Kotlin + Jetpack Compose Android 客户端；
- `gateway/`：Python + aiohttp 移动网关和管理网页；
- `fnos/`：飞牛 fnOS 应用中心安装包源码；
- `docs/`：Docker、NAS、fnOS、迁移与运维文档；
- `.github/`：持续集成、Issue 和 Pull Request 模板；

隐私说明、第三方依赖和安全报告方式分别见 [`PRIVACY.md`](PRIVACY.md)、[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) 与 [`SECURITY.md`](SECURITY.md)。

## 当前能力

- 多会话创建、切换、重命名和删除，定时任务执行会话不进入普通对话列表；
- 人物模型、实际调用模型与推理深度独立：人物仅接受 Hermes 明确标记的 persona，未选择时使用 `Hermes 默认（default）`；调用模型和 `reasoning_effort` 按对话单独保存；
- 可在手机端新建、编辑、删除、暂停、恢复和立即运行定时任务；
- 文字、图片、多文件批量选择与语音转文字，每个文件可独立重试或移除；
- Android 每次聊天都会声明手机端能力；Gateway 仅通过原版 Hermes HTTP API 注入本轮移动端上下文，避免把主机本地路径、桌面拖拽或电脑快捷键当作手机可用的文件交付方式；
- 浅色、深色、跟随系统；
- 未发送的草稿和附件可跨重启恢复；已提交消息会立即从草稿中移除，锁屏导致实时流断开时不回填输入框；
- 聊天输入使用 Android 原生焦点与输入连接，草稿采用低抖动尾随保存；页面直接采用输入法最终高度，不再跟随键盘动画逐帧重排长对话；
- 文件上传、下载、暂停、继续和取消；
- 后台回答状态与通知；发送后锁屏或临时切网时，App 通过 Gateway 运行状态续接，不重复提交消息；QQ、微信等其他渠道任务可显示“思考中”，外部任务保持只读且不能由 Nexus 停止；
- 长会话分页与一键回到底部；
- 移动网关账号登录，App 不保存 Hermes 主密钥；
- Nexus Gateway 提供 HTTP 源站；Android App 接受 HTTP 或 HTTPS 地址，公网部署仍建议由 Nginx、Caddy 等反向代理提供受信任的 HTTPS。
- Web 端定位为初始化与运维管理页，不再提供聊天；聊天、模型和定时任务由 Android App 完成。

## 架构

```text
Android App / 管理网页
        |
        | Bearer 设备令牌
        v
Nexus Gateway
        |
        | Hermes API Server Key（仅保存在网关）
        v
Hermes Agent API Server
```

移动网关不是 Hermes 的替代品。它负责移动端认证、附件存储、会话代理和后台运行状态。

## Hermes 原版兼容边界

- Hermes 是只读外部依赖；Nexus 禁止修改其源码、安装目录、虚拟环境、配置、模型路由、数据、日志、缓存或任何其他文件；
- Nexus 的脚本和测试禁止安装、升级、降级、卸载、启动、停止、重启或终止 Hermes；
- Android App 与 Gateway 只通过原版 Hermes HTTP API 工作，不依赖 fork、补丁、内部数据库或私有文件布局；
- 本地测试工具最多只读获取 Hermes API 地址与 Key，并把连接信息副本保存到 Nexus 自有目录，绝不回写 Hermes；
- 聊天、会话、模型查询和定时任务仍通过公开 API 正常工作；Hermes 自己如何持久化 API 状态不由 Nexus 直接操作；
- reset、upgrade、构建和部署只允许处理 Nexus 自有目录。完整强制规则见 [AGENTS.md](AGENTS.md)。

## 要求

Android：

- JDK 17；
- Android SDK 35；
- Android 8.0（API 26）或更高。

移动网关：

- 推荐：Docker Engine 与 Docker Compose；
- 源码运行可使用 Python 3.11 或更高；
- 可访问一个已启用 API Server 的 Hermes Agent 实例。

## 下载与发布物验证

正式版本通过 GitHub Releases 发布，包含：

- 官方签名的 Android Release APK；
- 可独立部署的 Gateway ZIP；
- 飞牛 fnOS 应用中心安装包 FPK 及独立 SHA-256 校验文件；
- `SHA256SUMS.txt`。

安装前应使用 `SHA256SUMS.txt` 核对 APK、Gateway ZIP 等发布附件。Android APK 由项目持久发布密钥签名，正式版本应保持相同签名系列。第三方许可声明保留在源码仓库与 Gateway ZIP 中，不再作为独立 Release 附件。

## 快速开始

### 1. 使用 Docker 启动移动网关（推荐）

```bash
docker compose config
docker compose build --pull
docker compose up -d
```

首次启动后，在本机或可信局域网打开 `http://网关地址:8787`，在初始化页面中设置：

- 管理员账号和密码；
- Hermes API Server 地址；
- Hermes API Server Key；
- `data/bootstrap.token` 中的一次性初始化令牌。

Nexus 会自动生成 Session Secret，并将配置保存到仓库根目录 `data/`。首次启动会创建 `data/bootstrap.token`，令牌不会通过公开 API 回显，初始化成功后文件会自动删除。首次部署不需要创建 `.env`。详细的 NAS、权限、备份、更新和旧网关迁移说明见 [`docs/docker-deployment.md`](docs/docker-deployment.md)。

也可以直接使用 Python 运行：

```bash
cd gateway
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python start_gateway.py
```

然后打开 `http://127.0.0.1:8787` 完成首次初始化。

健康检查：

```bash
curl http://127.0.0.1:8787/health
```

Nexus Gateway 不再内置 TLS 或证书管理。局域网直连只适合受信任网络；**不要把 HTTP 源站端口直接暴露到公网**。外网访问应使用反向代理提供受系统信任的 HTTPS 域名，并关闭代理缓冲以支持 SSE/流式回答。Nginx 和 Caddy 示例见 [`docs/docker-deployment.md`](docs/docker-deployment.md#6-https-反向代理)。

### 2. 安装飞牛 fnOS 应用包

飞牛 fnOS 用户可以在应用中心手动安装 `Nexus-fnOS-<版本>.fpk`。安装向导会收集 Nexus 登录账号、密码、Hermes API 地址和 API Server Key；敏感信息只保存到 Nexus 私有数据目录，不写入安装包或 Compose 明文环境变量。

Gateway 镜像固定为与源码版本一致的 GHCR 多架构标签。Windows 本地构建 FPK 不需要运行 Docker：

~~~powershell
./scripts/build_fnos_package.ps1
~~~

安装、升级、同机 Hermes 地址和真实设备验收说明见 [`docs/fnos-deployment.md`](docs/fnos-deployment.md)。

### 3. 构建 Android App

```bash
cd android
./gradlew testDebugUnitTest lintDebug assembleDebug
```

Windows 可运行：

```bat
gradlew.bat testDebugUnitTest lintDebug assembleDebug
```

本地 Debug APK 位于：

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

公开安装建议优先使用 GitHub Releases 中的官方签名 Release APK；本地 Debug APK 仅用于开发测试。安装后填写移动网关地址、账号和密码即可登录。App 接受显式 `http://` 或 `https://` 地址，不按公网/私网强制 HTTPS，也不显示证书或公网 HTTP 拦截提示；遗漏协议时只补全 `http://`，不会猜测或改写端口，用户填写的已保存地址也保持不变。公网明文 HTTP 会暴露账号、消息与 Token，生产环境仍建议使用反向代理 HTTPS。

## 测试

### 持续本地测试环境（无需 Docker）

本机已安装并启用 Hermes API Server 时，可一键创建隔离测试环境：

```bat
scripts\local-test.cmd setup
```

后续每次迭代使用 `upgrade` 同步 Nexus 依赖与上游连接信息副本并执行端到端冒烟测试，使用 `verify` 运行完整的非 Docker 回归门禁。该流程只读获取 Hermes 连接信息，不修改或管理 Hermes：

```bat
scripts\local-test.cmd upgrade
scripts\local-test.cmd verify
```

测试数据、日志和凭据全部保存在被 Git 忽略的 `.local-test/`。详细命令、升级约定和安全边界见 [`docs/local-test-environment.md`](docs/local-test-environment.md)。
另有独立的成品验收环境 `成品\本地测试环境\`：它只运行已构建的 Gateway ZIP，不读取源码，不调用 Docker，并在 HTTP `18787` 上提供测试服务。App 不会自动补全该端口，测试时需显式填写完整地址。`scripts\sync-product-test-environment.cmd` 只同步控制脚本和说明，不修改该环境的账号、配置、媒体、虚拟环境、日志、状态或历史 `data\tls`。

### 手动测试命令

移动网关：

```bash
cd gateway
pip install pytest pytest-asyncio aiohttp-cors
python -m pytest tests -q
```

网页契约：

```bash
node gateway/tests/web_contract_test.js
```

Android：

```bash
cd android
./gradlew testDebugUnitTest lintDebug assembleDebug
```

## 安全与数据

以下内容不得提交：

- `gateway/.env`；
- `.local-test/`；
- `gateway/data/`；
- Android 签名文件、`.release-signing/`、`local.properties`；
- `dist/` 与 `成品/` 中的本地构建产物；
- 用户上传文件、会话导出、日志和真实服务器地址；
- 任何 API Token、密码或会话密钥；
- 任何 Hermes 源码、安装文件、配置、数据、日志或缓存副本。

发现安全问题请按 `SECURITY.md` 私下报告，不要直接创建公开 Issue。

## 已知限制

- `0.1.x` 是首个公开稳定化系列，仍可能在后续次要版本调整非核心界面和部署细节；
- 尚无完整 UI 自动化和多厂商真机矩阵；
- 锁屏和后台通知会受到 Android 厂商电池策略影响；
- Gateway 只提供 HTTP 源站；公网证书、域名、反向代理、HSTS 和外部访问控制由部署者维护；

欢迎通过 Issue 报告 Bug，提交时请附版本、Android 系统、复现步骤和必要日志。请勿附带密码、Token、私人会话或用户文件。

## 许可证与品牌

源代码采用 Apache License 2.0，详见 `LICENSE`。第三方组件保留各自许可证，详见 `THIRD_PARTY_NOTICES.md`。

“Nexus”名称和项目标识不因代码开源而自动授权用于冒充官方发行版。分发修改版本时应清楚标注为社区版本或进行重命名，详见 `TRADEMARKS.md`。

本项目是独立社区项目，与 Hermes Agent 或 Nous Research 不存在官方隶属或背书关系。
