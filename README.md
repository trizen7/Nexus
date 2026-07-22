# Nexus

Nexus 是一个面向 Hermes Agent 的社区移动客户端与轻量移动网关。

项目目标是让 Hermes 核心保持原版、可独立升级，同时把移动端所需的登录、会话、附件、缓存、下载、通知和断线续接能力放在客户端与移动网关中。

当前版本：0.0.2

## 组成

- `android/`：Kotlin + Jetpack Compose Android 客户端；
- `gateway/`：Python + aiohttp 移动网关和管理网页；
- `docs/`：Docker、NAS、迁移、运维和开发计划文档；
- `.github/`：持续集成、Issue 和 Pull Request 模板；
- `文档/`：版本更新记录和分级开发待办。

开发方向与待办见 [`docs/development-roadmap.md`](docs/development-roadmap.md)。

## 当前能力

- 多会话创建、切换、重命名和删除；
- 文字、图片、普通文件和语音转文字；
- 浅色、深色、跟随系统；
- 草稿和附件跨重启恢复；
- 文件上传、下载、暂停、继续和取消；
- 后台回答状态与通知；
- 长会话分页与一键回到底部；
- 移动网关账号登录，App 不保存 Hermes 主密钥。

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

移动网关不是 Hermes 的替代品，也不会修改 Hermes 核心。它负责移动端认证、附件存储、会话代理和后台运行状态。

## 要求

Android：

- JDK 17；
- Android SDK 35；
- Android 8.0（API 26）或更高。

移动网关：

- 推荐：Docker Engine 与 Docker Compose；
- 源码运行可使用 Python 3.11 或更高；
- 可访问一个已启用 API Server 的 Hermes Agent 实例。

## 快速开始

### 1. 使用 Docker 启动移动网关（推荐）

```bash
docker compose config
docker compose build --pull
docker compose up -d
```

首次启动后打开 `http://网关地址:8787`，在初始化页面中设置：

- 管理员账号和密码；
- Hermes API Server 地址；
- Hermes API Server Key。
- `data/bootstrap.token` 中的一次性初始化令牌。

Nexus 会自动生成 Session Secret，并将配置保存到仓库根目录 `data/`。首次启动会创建 `data/bootstrap.token`，令牌不会通过公开 API 回显，初始化成功后文件会自动删除。首次部署不需要创建 `.env`。详细的 NAS、权限、备份、更新和旧网关迁移说明见 `docs/docker-deployment.md`。

也可以直接使用 Python 运行：

```bash
cd gateway
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python start_gateway.py
```

然后打开 `http://127.0.0.1:8787` 完成首次初始化。`.env.example` 仅用于无网页环境、旧部署兼容或自动化预配置。

健康检查：

```bash
curl http://127.0.0.1:8787/health
```

生产环境请使用 HTTPS 反向代理，不要把明文 HTTP 和管理端口直接暴露到公网。

### 2. 构建 Android App

```bash
cd android
./gradlew testDebugUnitTest lintDebug assembleDebug
```

Windows 可运行：

```bat
gradlew.bat testDebugUnitTest lintDebug assembleDebug
```

Debug APK 位于：

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

安装后填写移动网关地址、账号和密码即可登录。

## 测试

### 持续本地测试环境（无需 Docker）

本机已安装并启用 Hermes API Server 时，可一键创建隔离测试环境：

```bat
scripts\local-test.cmd setup
```

后续每次迭代使用 `upgrade` 同步依赖与 Hermes 配置并执行端到端冒烟测试，使用 `verify` 运行完整的非 Docker 回归门禁：

```bat
scripts\local-test.cmd upgrade
scripts\local-test.cmd verify
```

测试数据、日志和凭据全部保存在被 Git 忽略的 `.local-test/`。详细命令、升级约定和安全边界见 [`docs/local-test-environment.md`](docs/local-test-environment.md)。

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
- Android 签名文件、`local.properties`；
- 用户上传文件、会话导出、日志和真实服务器地址；
- 任何 API Token、密码或会话密钥。

发现安全问题请按 `SECURITY.md` 私下报告，不要直接创建公开 Issue。

## 已知限制

- 当前仍处于早期稳定期，版本号保持在 `0.0.x`；
- 尚无完整 UI 自动化和多厂商真机矩阵；
- 锁屏和后台通知会受到 Android 厂商电池策略影响；
- Release/AAB、应用商店发布和正式签名流程尚未标准化；
- HTTPS 终止由部署者的反向代理负责；
- 旧星禾版不再独立维护，后续功能、Bug 修复和发布统一进入 Nexus；
- Nexus 使用新包名，可与旧星禾版并存；本地草稿和缓存不会自动迁移。

欢迎通过 Issue 报告 Bug，提交时请附版本、Android 系统、复现步骤和必要日志。请勿附带密码、Token、私人会话或用户文件。

项目进展见：

- [`文档/05-版本更新记录.md`](文档/05-版本更新记录.md)；
- [`文档/06-开发计划与待办.md`](文档/06-开发计划与待办.md)。

## 许可证与品牌

源代码采用 Apache License 2.0，详见 `LICENSE`。

“Nexus”名称和项目标识不因代码开源而自动授权用于冒充官方发行版。分发修改版本时应清楚标注为社区版本或进行重命名，详见 `TRADEMARKS.md`。

星禾人格、名称、视觉和声音属于 Xinghe Edition／星禾版，不包含在通用 Nexus 开源项目中。

本项目是独立社区项目，与 Hermes Agent 或 Nous Research 不存在官方隶属或背书关系。
