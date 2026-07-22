# 更新记录

本项目遵循 `0.0.x` 小步版本规则。

## 未发布

- 暂无。

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
