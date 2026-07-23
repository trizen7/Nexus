# 更新记录

本项目遵循 `0.0.x` 小步版本规则。

## 未发布

- 暂无。

## 0.0.7｜HTTP 源站与 HTTPS 反向代理

- Gateway 改回单一 HTTP 源站监听，不再生成、读取或热更新 TLS 证书，并移除临时 CA 下载与网页证书管理入口；旧 `/api/admin/tls` 路径不再转发给 Hermes；
- 外网部署改由 Nginx、Caddy 或其他反向代理提供受信任的 HTTPS 域名，文档补充 `Host`、`X-Forwarded-Proto`、流式响应关闭缓冲、长读取超时及 HSTS 边界；HTTP 源站端口不得直接暴露到公网；
- Android 允许回环、RFC 1918 私网、链路本地、CGNAT、IPv6 ULA 和 `.local` 地址使用 HTTP，拒绝公网 HTTP；裸私网地址默认补全 `http://地址:18787`，裸公网域名默认补全 `https://域名`；
- Android 自动把旧保存的私网 `https://地址:18788` 迁移为 `http://地址:18787`，移除 Debug CA 生成、内嵌和安装依赖；反向代理 HTTPS 继续使用系统信任链；
- 人物模型、实际调用模型、推理深度和手机端定时任务管理继续保留；普通对话列表继续隐藏定时任务执行会话；
- 独立成品测试环境切换到 LocalSubnet TCP `18787`，升级时安全停止旧 `18788` 进程并确认旧端口关闭；普通 upgrade 继续保留账号、Hermes 配置、媒体、运行数据及历史 `data/tls`，reset 才清空数据；
- Docker、Compose、环境变量示例和源码本地测试脚本同步为 HTTP 源站；按要求未在本机运行 Docker；
- 自动验证：Gateway 77 项通过、9 项按环境跳过，Python 编译、网页契约与 JavaScript 语法检查通过；Android 126 项单元测试全部通过，`lintDebug` 与 Debug APK 构建通过；
- 已生成 `Nexus-Android-0.0.7-debug.apk` 与 `Nexus-Gateway-0.0.7.zip`，并验证 Gateway ZIP 不含运行数据、缓存、私钥、测试 CA 或 Android 构建文件；
- 独立测试环境已从 0.0.6 无损升级到 0.0.7：本机与局域网 HTTP 健康检查通过，`18787` 监听于 `0.0.0.0`、`18788` 完全关闭，账号、Hermes 配置、媒体和历史 TLS 文件保持不变。

## 0.0.6｜推理深度与 HTTPS 证书管理

- Android 在人物模型和实际调用模型之外新增独立推理深度选择，支持默认、`none`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`、`ultra` 并持久保存；
- Gateway 对 `reasoning_effort` 做白名单校验并透传；Nexus 不保证 Hermes 或所选调用模型一定支持、采用该字段；
- Gateway 改为单一 HTTPS 监听，禁止 HTTP，自动生成并持久化临时 CA/服务器证书，TLS 最低版本为 1.2；当前无需提供正式证书，后续可在网页上传正式证书；
- 管理网页新增 HTTPS 证书状态与上传入口，可校验并热切换正式 PEM 证书链和未加密 PEM 私钥，失败自动回滚；
- Android 拒绝明文 HTTP，裸局域网 IP 默认补全 `https://` 与端口 `18788`；
- Android 与网页普通对话列表均隐藏 `source=cron` 的定时任务执行会话，定时任务继续通过专用管理界面操作；
- 0.0.6 Debug APK 在构建时内嵌成品测试环境 CA，手机无需手动安装证书；Release APK 仍只信任系统 CA；
- 独立成品测试环境只开放 LocalSubnet TCP `18788`，升级保留账号、Hermes 配置、媒体和整个 `data/tls`，只有 reset 才删除 CA；
- Docker 与源码本地测试脚本同步改为 HTTPS-only；按要求未运行 Docker 本机测试；
- 自动验证：Gateway 87 项通过、9 项按环境跳过，Python 编译、网页契约和 JavaScript 语法检查通过；Android 126 项单元测试、`lintDebug` 与 Debug APK 构建通过；
- 已生成 `Nexus-Android-0.0.6-debug.apk` 与 `Nexus-Gateway-0.0.6.zip`，并验证 APK 内嵌 CA 与独立测试环境当前 CA 一致、Gateway ZIP 不含运行数据或私钥；
- 独立测试环境已无损升级到 0.0.6：本机与 `10.0.0.123` 的 HTTPS 健康检查通过，`18788` 正常监听、`18787` 完全关闭，账号、Hermes 配置、媒体和 TLS 数据保持不变。

## 0.0.5｜Android 连接兼容与登录恢复

- 修复 Android Debug APK 连接本地 HTTPS 时不信任用户手动安装 CA 的问题；Release 构建仍只信任系统 CA；
- 连接页明确区分本地测试的 App API 端口 `18787` 与 HTTPS 网页端口 `18788`，并为遗漏协议的地址自动补全 `http://`；
- 登录前统一规范化并校验服务器地址，拒绝非 HTTP(S) 协议、账号信息、查询参数和锚点；
- 连接错误细分为账号或密码错误、设备 Token 失效、证书校验失败、DNS、拒绝连接、超时和连接中断，并提供本地 CA 下载或 HTTP 恢复路径；
- 已保存 Token 失效时自动清除无效凭据并返回登录页，用户重新输入密码后可重新签发设备 Token，不再反复使用旧 Token；
- 普通请求、停止回答和流式对话统一保留 HTTP 状态及 Gateway 错误信息，401 可可靠触发重新登录；
- 自动验证：Gateway 68 项通过、9 项按环境跳过，Python 编译和网页契约通过；Android 122 项 JVM 测试通过，Lint 0 issues，Debug APK 构建成功；
- 独立成品测试环境无损升级到 0.0.5，账号、Hermes 配置、媒体和本地 HTTPS CA 保持不变，HTTP/HTTPS 健康检查与局域网双端口监听通过。

## 0.0.4｜人物与调用模型拆分、内置 HTTPS

- Android 将“人物模型”和“调用模型”拆分为两个独立选择：人物模型保留角色设定，调用模型决定实际推理模型，并支持“使用 Hermes 默认”；
- 模型列表按 Hermes `parent` 元数据分类，旧版单一模型偏好自动迁移为人物模型偏好；
- Android 发送 `persona_model` 与 `inference_model`，Gateway 继续兼容旧 `model` 字段；
- Gateway 在选择调用模型时使用 Hermes OpenAI 兼容流式接口，未选择调用模型时保留原生 Session Chat 与人物模型路由；
- Gateway 新增可选内置 HTTPS 监听、最低 TLS 1.2、网页 HTTP→HTTPS 跳转和本地 CA 下载接口；
- 独立成品测试环境使用 HTTP `18787` 提供 Android/兼容 API、HTTPS `18788` 提供网页，并持久保存本地 CA；
- 测试环境控制文件纳入 `scripts/product-test-environment/` 维护，安全同步只更新控制脚本，不修改账号、配置、媒体、虚拟环境、日志或状态；
- Hermes 本地测试配置新增 `gpt-5.6-sol` 调用模型路由，人物模型“星禾”继续保留。
- 自动验证：Gateway 68 项通过、9 项按环境跳过，Python 编译和网页契约通过；Android 116 项 JVM 测试通过，Lint 无 Error，Debug APK 构建成功；
- 独立环境完成两轮无损升级，账号、Hermes 配置、媒体和 3 个 Hermes 定时任务保持不变，本地 CA 指纹在第二轮升级中保持一致；本机与局域网 HTTPS、HTTP 跳转、双端口监听和 LocalSubnet 防火墙规则均验证通过。

## 0.0.3｜移动模型选择与定时任务管理

- Android 对话列表不再显示 Hermes 定时任务执行会话；
- 手机端可读取 Hermes 模型列表、选择聊天模型并持久保存选择；
- Nexus Gateway 在指定模型时使用 Hermes OpenAI 兼容流式接口，并转换为现有移动端事件协议；未指定模型时保持原生 Session Chat 路由；
- 手机端新增独立定时任务管理页，支持列表、刷新、新建、编辑、删除、暂停、恢复和立即运行；
- 定时任务表单支持 Cron、固定间隔和 ISO 单次时间，并显示输入校验及服务器错误；
- Gateway 自动验证：65 项通过、9 项跳过；
- Android 自动验证：115 项 JVM 单元测试通过，Lint 无 Error，Debug APK 构建成功。

## 0.0.2｜安全与多会话稳定性加固

- Nexus 设备令牌仅发送到同源网关，外部图片和下载地址不再携带认证；
- HTTP 登录增加明文风险确认，并保留可信局域网 HTTP 自用能力；
- 首次初始化使用一次性 Bootstrap Token，配置或账号文件损坏时 fail closed；
- 管理员密码使用 scrypt 加盐哈希，旧明文账号可在成功登录后迁移；
- 登录增加来源 IP 限速；
- 支持多个会话同时进行本机后台状态监控，登出不停止 Hermes 服务端任务；
- 加固会话缓存、通知标识、持久 URI、SSE JSON 解析和锁屏通知隐私；
- 网关增加上传配额、磁盘低水位、绝对路径隐藏和错误脱敏；
- 修复 GitHub Actions Android Wrapper 权限与 Gateway 测试工作目录问题；
- Gradle Wrapper 增加官方 SHA-256 校验。

## 0.0.1｜首个 Nexus 开源版本

- 增加首次初始化向导：首次启动可在网页创建管理员账号，并配置 Hermes API 地址与 API Server Key；
- Docker Compose 首次部署不再要求预先创建 `.env`，配置与账号统一持久化到 `data/`；
- 兼容旧 `.env` 部署，启动时自动生成持久化 `config.json`；
- 项目统一命名为 Nexus；
- Android 应用名为 Nexus，包名为 `app.nexus.mobile`；
- 移动网关模块为 `nexus_gateway`，环境变量前缀为 `NEXUS_`；
- 提供 Android 多会话、附件、下载、通知、草稿和长会话分页能力；
- 提供 Nexus Gateway 账号认证、Hermes 会话代理、SSE、媒体和 Web 管理能力；
- 增加 Dockerfile、Docker Compose、容器健康检查、非 root 用户、持久化目录和日志轮转；
- 增加飞牛 NAS 部署、备份、更新及旧星禾网关迁移说明；
- 增加 Android、网关、网页契约和 Docker 构建 CI；
- 星禾移动端 v0.0.20 冻结为历史最终版，后续开发统一进入 Nexus。
