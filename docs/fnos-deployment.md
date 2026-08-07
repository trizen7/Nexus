# 飞牛 fnOS 部署

Nexus Gateway 提供飞牛 fnOS 原生应用包。安装包只部署 Nexus 自有 Gateway 运行时和数据目录；Hermes 始终是原版、只读外部依赖，Nexus 只通过 Hermes HTTP API 与其通信。

## 发布物

同一版本提供两个架构专用安装包：

- x86_64 / amd64 NAS：`Nexus-fnOS-0.1.8-amd64.fpk`
- ARM64 / aarch64 NAS：`Nexus-fnOS-0.1.8-arm64.fpk`
- 统一校验文件：`SHA256SUMS.txt`
- 默认 HTTP 端口：`8787`

当前 fnOS 包版本为 `0.1.8`，与 Gateway `0.1.8` 完全一致。每个 FPK 都内置对应架构的 Gateway 原生可执行运行时和 CA 信任库，不包含 Docker 镜像、Compose 项目或在线下载器。

**fnOS 设备不需要安装 Docker，也不需要授予 Nexus Docker 权限。** 安装、升级和启动均不会访问 GitHub、GHCR、Docker Hub 或其他容器仓库。GHCR/Docker 镜像仅作为普通 Gateway 部署的可选渠道，不是 fnOS FPK 的依赖。

fnOS 的 `8787` 是正式应用默认端口。仓库中成品测试环境使用的 `18787` 只属于 Windows 本地验收环境，与 fnOS 安装包无关。

## 安装前准备

1. 根据 NAS CPU 架构下载正确的 amd64 或 arm64 FPK，并使用 `SHA256SUMS.txt` 校验。
2. 用户已经独立运行原版 Hermes API Server。
3. 已取得 Hermes API 地址和 API Server Key。
4. NAS 的 `8787` 端口未被其他服务占用，并为 FPK、临时解包和 Nexus 数据预留足够空间。

Hermes 与 Nexus 位于同一台 NAS 时，fnOS 版 Nexus 作为主机原生进程运行，可直接访问 Hermes 默认的回环监听地址：

~~~text
http://127.0.0.1:8642
~~~

如果用户已为 Hermes API 修改端口，请将 `8642` 换成实际端口。Hermes 位于另一台设备时，填写该设备对 NAS 可达的局域网地址。

从旧版升级时，Nexus 只迁移自己私有配置副本中的同机地址：`host.docker.internal`、`localhost`、IPv4 回环或 IPv6 回环会规范化为 `127.0.0.1`，并保留原协议、端口、路径、Key 和 Session Secret。`0.0.0.0` 与 `::` 是监听地址，不是可连接的 Hermes 目标，因此会被拒绝。迁移不会读取或修改任何 Hermes 文件。

## 安装

1. 在飞牛 fnOS 应用中心选择手动安装，上传与 NAS 架构匹配的 `.fpk`。
2. 设置 Nexus 登录账号和至少 8 个字符的密码。
3. 填写 Hermes API 地址和 Hermes API Server Key。
4. 安装前预检只读校验临时解包运行时的文件集合、SHA-256、ELF 格式和 CPU 架构，不创建或写入 `TRIM_PKGVAR`。
5. fnOS 直接启动包内 Gateway 原生可执行文件，不调用 Docker，也不联网下载组件。
6. 从应用中心打开 Nexus，或访问 `http://<NAS 地址>:8787/`。
7. Android App 中填写相同的 Nexus 地址、账号和密码。

安装向导值会先以仅包用户可读的一次性文件写入 Nexus 自有 `TRIM_PKGVAR`。Gateway 首次启动时使用 scrypt 保存密码散列、生成随机 Session Secret，然后删除一次性文件；明文密码不会保留在正式配置中。

## 修改配置

在应用中心打开 Nexus 的配置页。以下字段均可单独修改，留空表示保持原值：

- Nexus 账号
- Nexus 密码
- Hermes API 地址
- Hermes API Server Key

修改账号或密码后，Nexus 会递增账号修订号，已登录设备需要重新登录。配置回调只停止并重新启动 Nexus 自己的 Gateway 原生进程，不会读取、修改、启动、停止或重启 Hermes。

## 数据、进程与安全

Nexus 账号、密码散列、Session Secret、Hermes API 地址、Hermes Key、媒体、PID 和日志只写入 fnOS 分配的 Nexus 私有 `TRIM_PKGVAR`。软件包不申请共享目录，不直接访问 Hermes 的源码、安装目录、配置、数据库、会话、任务、日志、缓存或进程。

生命周期脚本只管理由当前 Nexus 包启动且同时匹配 PID、进程启动时间和包内可执行文件路径的进程。停止时先发送 TERM，超时后才发送 KILL；不会扫描、终止或管理 Hermes 及其他进程。

Gateway 提供 HTTP 源站。受信任局域网可直接使用 `http://<NAS 地址>:8787`；需要公网访问时，应由用户自己的 Nginx、Caddy 或其他反向代理提供受信任的 HTTPS 和访问控制。

## 升级

升级 FPK 前应先备份 Nexus 私有数据目录。fnOS 只替换应用目录中的 Gateway 运行时，持久化数据继续保留在 `TRIM_PKGVAR`。升级脚本会先校验新运行时，再由 fnOS 重启 Nexus；整个过程不访问 GitHub、GHCR、Docker Hub，不调用 Docker，也不接触 Hermes。

每次发布按以下顺序更新：

1. 更新 Gateway `__version__`、Android 版本和 Compose 可选部署标签。
2. 更新 `fnos/nexus-gateway/manifest` 的 fnOS 修订版本。
3. 分别构建 `linux/amd64`、`linux/arm64` 的原生 Gateway 运行时。
4. 将两个运行时分别封装为架构专用 FPK，生成统一 `SHA256SUMS.txt`。
5. 执行静态契约验证，并在真实 fnOS 设备上完成安装、升级、停止、启动和卸载测试。
6. 如有需要，另行发布普通 Docker 用户可选的 GHCR 多架构镜像；该步骤不影响 FPK。

## 本地构建 FPK

本机构建脚本不会运行 Docker。它要求维护者预先提供一个对应架构的原生运行时目录。该目录必须只包含：

~~~text
<runtime>/
  ca-certificates.crt
  nexus-gateway/
    nexus-gateway
    _internal/
      ...
~~~

正式双架构运行时由 GitHub Actions 使用 `gateway/FnOS.Dockerfile` 和 Buildx 构建并导出；Buildx 只用于构建阶段，生成的 FPK 本身不包含或依赖 Docker。

Windows 示例：

~~~powershell
./scripts/build_fnos_package.ps1 `
  -Platform amd64 `
  -RuntimeDirectoryPath .local-test\runtime\amd64 `
  -OutputDirectory dist

./scripts/build_fnos_package.ps1 `
  -Platform arm64 `
  -RuntimeDirectoryPath .local-test\runtime\arm64 `
  -OutputDirectory dist
~~~

也可以通过 `-FnpackPath` 显式指定已下载并校验的 `fnpack 1.2.3`。输出为：

~~~text
dist/Nexus-fnOS-0.1.8-amd64.fpk
dist/Nexus-fnOS-0.1.8-arm64.fpk
dist/SHA256SUMS.txt
~~~

构建后执行无解包静态验收：

~~~powershell
.local-test\venv\Scripts\python.exe scripts\verify_fnos_package.py `
  dist\Nexus-fnOS-0.1.8-amd64.fpk `
  --sha256-file dist\SHA256SUMS.txt

.local-test\venv\Scripts\python.exe scripts\verify_fnos_package.py `
  dist\Nexus-fnOS-0.1.8-arm64.fpk `
  --sha256-file dist\SHA256SUMS.txt
~~~

验证器会检查 FPK 文件名与 manifest 架构、运行时文件集合、逐文件 SHA-256、ELF 格式和 CPU 架构、执行权限、生命周期脚本、远程访问禁令、图标、LF 换行、许可证、明文凭据和 Hermes 只读边界。

## 真实设备验收

FPK 格式和静态契约可以在仓库中自动验证，但最终发布前仍需在真实飞牛 fnOS 设备上检查：

- x86_64 设备安装 amd64 FPK，ARM64 设备安装 arm64 FPK
- 未安装 Docker、且无法访问 GitHub/GHCR/Docker Hub 时，仍可干净安装、升级、停止、启动和卸载
- 安装日志中没有 `docker load`、镜像拉取或在线下载
- 错误架构 FPK 会在启动前明确失败
- 同机及异机 Hermes 地址的连通性
- 错误 URL、错误 Key 和 Hermes 不可达时的提示
- 单独修改账号、密码、URL 或 Key
- 升级后 Nexus 数据保留，账号修改后旧登录令牌按预期失效
- 应用中心图标、说明、配置页和桌面入口显示

真实设备验收也不得安装、升级、停止、启动、重启、修改或读取 Hermes 文件与进程；只能连接用户已经运行的原版 Hermes HTTP API。
