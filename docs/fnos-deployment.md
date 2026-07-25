# 飞牛 fnOS 部署

Nexus Gateway 提供飞牛 fnOS Docker 应用包。安装包只部署 Nexus 自有网关和数据目录；Hermes 始终是原版、只读外部依赖，Nexus 只通过 Hermes HTTP API 与其通信。

## 发布物

- 安装包：`Nexus-fnOS-<版本>.fpk`
- 校验文件：同名 `.sha256` 文件
- 容器镜像：`ghcr.io/trizen7/nexus-gateway:<Gateway 版本>`
- 默认主机端口：`18787`

首个 fnOS 集成修订为 `0.1.0-fnos1`，对应 Gateway `0.1.0` 和镜像标签 `0.1.0`。后续 Gateway 版本升级时，必须同步更新 manifest、Compose 镜像标签、测试和发布物。

## 安装前准备

1. 飞牛 fnOS 已安装并启用 Docker，且能够访问 GitHub Container Registry。
2. 用户已经独立运行原版 Hermes API Server。
3. 已取得 Hermes API 地址和 API Server Key。
4. NAS 的 `18787` 端口未被其他服务占用。

Hermes 与 Nexus 位于同一台 NAS 时，容器内不能使用 `127.0.0.1` 访问宿主机，可填写：

~~~text
http://host.docker.internal:<Hermes API 端口>
~~~

Hermes 位于另一台设备时，填写该设备对 NAS 可达的局域网地址。

## 安装

1. 在飞牛 fnOS 应用中心选择手动安装，上传 `.fpk`。
2. 设置 Nexus 登录账号和至少 8 个字符的密码。
3. 填写 Hermes API 地址和 Hermes API Server Key。
4. 安装完成后，fnOS 会拉取多架构 Gateway 镜像并启动 `nexus-gateway-fnos` 容器。
5. 从应用中心打开 Nexus，或访问 `http://<NAS 地址>:18787/`。
6. Android App 中填写相同的 Nexus 地址、账号和密码。

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

容器以 fnOS 分配的 `TRIM_UID:TRIM_GID` 运行，根文件系统只读，`/tmp` 使用内存文件系统，并移除 Linux capabilities、启用 `no-new-privileges`。容器仅把 Nexus 自有数据目录挂载到 `/data`。

Gateway 提供 HTTP 源站。局域网可直接使用 `http://<NAS 地址>:18787`；需要公网访问时，应由用户自己的 Nginx、Caddy 或其他反向代理提供受信任的 HTTPS 和访问控制。

## 升级

升级 FPK 前应先备份 Nexus 私有数据目录。升级包会保留 `TRIM_PKGVAR`，并使用新包中的 Compose 定义重新创建 Nexus Docker 项目；不会接触 Hermes。

每次发布按以下顺序更新：

1. 更新 Gateway `__version__`。
2. 更新 `fnos/nexus-gateway/manifest` 的 fnOS 修订版本。
3. 更新 FPK Compose 中固定的 GHCR 镜像标签。
4. 发布相同 Gateway 版本的多架构 GHCR 镜像。
5. 构建 FPK，校验 SHA-256，并在真实 fnOS 设备上完成升级测试。

## 本地构建 FPK

Windows 构建不需要本机 Docker。脚本会使用固定版本的官方 `fnpack 1.2.3`，校验工具 SHA-256，把打包副本统一为 UTF-8/LF，再生成 FPK：

~~~powershell
./scripts/build_fnos_package.ps1
~~~

也可以显式指定已下载的 fnpack：

~~~powershell
./scripts/build_fnos_package.ps1 `
  -FnpackPath C:\tools\fnpack-1.2.3-windows-amd64.exe `
  -OutputDirectory dist
~~~

输出：

~~~text
dist/Nexus-fnOS-0.1.0-fnos1.fpk
dist/Nexus-fnOS-0.1.0-fnos1.fpk.sha256
~~~

构建后可执行无解包静态验收，检查归档路径、文件集合、图标、版本、镜像标签、LF 换行、许可证、SHA-256、明文凭据和 Hermes 只读边界：

~~~powershell
.local-test\venv-fnos\Scripts\python.exe scripts\verify_fnos_package.py `
  dist\Nexus-fnOS-0.1.0-fnos1.fpk
~~~

GitHub Actions 的容器工作流会在 Ubuntu Runner 中构建并发布 `linux/amd64`、`linux/arm64` 镜像，再使用校验过的 Linux fnpack 构建 FPK。仓库不要求在开发机上运行 Docker。

## 真实设备验收

FPK 格式和静态契约可以在仓库中自动验证，但最终发布前仍需在真实飞牛 fnOS 设备上检查：

- 干净安装、停止、启动和卸载流程
- x86_64 与 ARM64 设备的镜像拉取和启动
- 同机及异机 Hermes 地址的连通性
- 错误 URL、错误 Key 和 Hermes 不可达时的提示
- 单独修改账号、密码、URL 或 Key
- 升级后 Nexus 数据保留，旧登录令牌按预期失效
- 应用中心图标、说明、配置页和桌面入口显示

参考飞牛开发者文档中的 Docker 应用案例、Manifest、Wizard、Resource、fnpack 和发布流程。
