# 飞牛 fnOS 部署

Nexus Gateway 提供飞牛 fnOS Docker 应用包。安装包只部署 Nexus 自有网关和数据目录；Hermes 始终是原版、只读外部依赖，Nexus 只通过 Hermes HTTP API 与其通信。

## 发布物

同一版本会提供两个架构专用安装包：

- x86_64 / amd64 NAS：`Nexus-fnOS-0.1.5-fnos2-amd64.fpk`
- ARM64 / aarch64 NAS：`Nexus-fnOS-0.1.5-fnos2-arm64.fpk`
- 统一校验文件：`SHA256SUMS.txt`
- 默认主机端口：`8787`

当前 fnOS 集成修订为 `0.1.5-fnos2`，对应 Gateway `0.1.5`。每个 FPK 都内置该架构完整的 `nexus-gateway-fnos:0.1.5` Docker save gzip 镜像，因此安装、升级和启动时不会访问 GitHub、GHCR 或 Docker Hub，也不会执行 `docker pull`。GHCR 镜像仅作为普通 Docker 部署的可选渠道，不是 fnOS 安装包的运行依赖。

由于完整容器镜像已放进 FPK，自包含安装包会明显大于旧的在线拉取版，这是正常现象。

## 安装前准备

1. 飞牛 fnOS 已安装并启用 Docker。
2. 根据 NAS CPU 架构下载正确的 amd64 或 arm64 FPK，并使用 `SHA256SUMS.txt` 校验。
3. 用户已经独立运行原版 Hermes API Server。
4. 已取得 Hermes API 地址和 API Server Key。
5. NAS 的 `8787` 端口未被其他服务占用，并为 FPK、临时解包和 Docker 镜像预留足够空间。

Hermes 与 Nexus 位于同一台 NAS 时，fnOS 版 Nexus 使用主机网络，可直接访问 Hermes 默认的回环监听地址：

~~~text
http://127.0.0.1:8642
~~~

如果用户已为 Hermes API 修改端口，请将 `8642` 换成实际端口。Hermes 位于另一台设备时，填写该设备对 NAS 可达的局域网地址。

从旧版本升级时，容器入口会把 Nexus 私有配置中的 `host.docker.internal`、`localhost`、IPv4 回环或 IPv6 回环地址规范化为 `127.0.0.1`，并保留原协议、端口、路径、Key 和 Session Secret。`0.0.0.0` 与 `::` 是监听用的未指定地址，不是可连接的 Hermes 目标，会被拒绝。迁移只写入 Nexus 的 fnOS 私有数据目录，不读取或修改任何 Hermes 文件。

## 安装

1. 在飞牛 fnOS 应用中心选择手动安装，上传与 NAS 架构匹配的 `.fpk`。
2. 设置 Nexus 登录账号和至少 8 个字符的密码。
3. 填写 Hermes API 地址和 Hermes API Server Key。
4. 安装脚本先校验包内镜像的 SHA-256 与架构，再通过 `docker load` 导入本地镜像；不会连接任何容器仓库。
5. fnOS 使用包内 Compose 定义启动 `nexus-gateway-fnos` 容器。
6. 从应用中心打开 Nexus，或访问 `http://<NAS 地址>:8787/`。
7. Android App 中填写相同的 Nexus 地址、账号和密码。

安装向导不会把密码或 Hermes Key 写入 Compose 环境变量。向导值会先以仅包用户可读的一次性文件写入 Nexus 自有 `TRIM_PKGVAR`；容器首次启动后，密码会使用 scrypt 散列保存，同时生成随机 Session Secret，并删除一次性文件。

## 修改配置

在应用中心打开 Nexus 的配置页。以下字段均可单独修改，留空表示保持原值：

- Nexus 账号
- Nexus 密码
- Hermes API 地址
- Hermes API Server Key

修改账号或密码后，Nexus 会递增账号修订号，已登录设备需要重新登录。配置回调只重启 `nexus-gateway-fnos`，不会读取、修改、启动、停止或重启 Hermes。

## 数据与安全

Nexus 账号、密码散列、Session Secret、Hermes API 地址、Hermes Key、媒体和运行数据只写入 fnOS 分配的 Nexus 私有 `TRIM_PKGVAR`。软件包不申请共享目录，不直接访问 Hermes 的源码、安装目录、配置、数据库、会话、任务、日志、缓存或进程。

容器以 fnOS 分配的 `TRIM_UID:TRIM_GID` 运行，根文件系统只读，`/tmp` 使用内存文件系统，并移除 Linux capabilities、启用 `no-new-privileges`。容器仅把 Nexus 自有数据目录挂载到 `/data`。容器共享 NAS 主机网络以访问 Hermes 回环 API，不再使用端口映射或 `host.docker.internal`；这不会挂载、读取或改写 Hermes 文件。

Gateway 提供 HTTP 源站。局域网可直接使用 `http://<NAS 地址>:8787`；需要公网访问时，应由用户自己的 Nginx、Caddy 或其他反向代理提供受信任的 HTTPS 和访问控制。

## 升级

升级 FPK 前应先备份 Nexus 私有数据目录。升级脚本会校验并导入新 FPK 内置的本地镜像，然后由 fnOS 使用新 Compose 定义重新创建 Nexus Docker 项目。升级不会访问 GitHub/GHCR，不会改变 `TRIM_PKGVAR`，也不会接触 Hermes。

每次发布按以下顺序更新：

1. 更新 Gateway `__version__`。
2. 更新 `fnos/nexus-gateway/manifest` 的 fnOS 修订版本。
3. 更新 FPK Compose 和生命周期脚本中的本地镜像标签。
4. 分别构建 `linux/amd64`、`linux/arm64` 的 Docker save gzip 镜像归档。
5. 将两个归档分别封装为架构专用 FPK，校验统一 SHA-256，并在真实 fnOS 设备上完成安装与升级测试。
6. 如有需要，另行发布普通 Docker 用户可选的 GHCR 多架构镜像；该步骤不影响 FPK 安装。

## 本地构建 FPK

本机构建脚本不会运行 Docker。它要求维护者预先提供一个 gzip 压缩的 Docker save 归档，归档内必须只有对应架构的 `nexus-gateway-fnos:0.1.5` 镜像。正式归档通常由 GitHub Actions 的 Buildx 环境生成。

Windows 示例：

~~~powershell
./scripts/build_fnos_package.ps1 `
  -Platform amd64 `
  -ImageArchivePath .local-test\images\nexus-gateway-amd64.tar.gz `
  -OutputDirectory dist

./scripts/build_fnos_package.ps1 `
  -Platform arm64 `
  -ImageArchivePath .local-test\images\nexus-gateway-arm64.tar.gz `
  -OutputDirectory dist
~~~

也可以通过 `-FnpackPath` 显式指定已下载并校验的 `fnpack 1.2.3`。输出为：

~~~text
dist/Nexus-fnOS-0.1.5-fnos2-amd64.fpk
dist/Nexus-fnOS-0.1.5-fnos2-arm64.fpk
dist/SHA256SUMS.txt
~~~

构建后执行无解包静态验收：

~~~powershell
.local-test\venv\Scripts\python.exe scripts\verify_fnos_package.py `
  dist\Nexus-fnOS-0.1.5-fnos2-amd64.fpk `
  --sha256-file dist\SHA256SUMS.txt

.local-test\venv\Scripts\python.exe scripts\verify_fnos_package.py `
  dist\Nexus-fnOS-0.1.5-fnos2-arm64.fpk `
  --sha256-file dist\SHA256SUMS.txt
~~~

验证器会检查 FPK 文件名和 manifest 架构、包内镜像标签和架构、镜像归档 SHA-256、Compose 的 `pull_policy: never`、远程下载指令、归档路径、文件集合、图标、LF 换行、许可证、明文凭据和 Hermes 只读边界。

## 真实设备验收

FPK 格式和静态契约可以在仓库中自动验证，但最终发布前仍需在真实飞牛 fnOS 设备上检查：

- x86_64 设备安装 amd64 FPK，ARM64 设备安装 arm64 FPK
- 断开 GitHub、GHCR 和 Docker Hub 访问后仍可干净安装、升级、停止、启动和卸载
- 安装日志中没有镜像拉取，并能从包内镜像正常创建容器
- 错误架构 FPK 会在加载镜像前明确失败
- 同机及异机 Hermes 地址的连通性
- 错误 URL、错误 Key 和 Hermes 不可达时的提示
- 单独修改账号、密码、URL 或 Key
- 升级后 Nexus 数据保留，旧登录令牌按预期失效
- 应用中心图标、说明、配置页和桌面入口显示

真实设备验收也不得安装、升级、停止、启动、重启、修改或读取 Hermes 文件与进程；只能连接用户已经运行的原版 Hermes HTTP API。
