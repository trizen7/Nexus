# 更新记录

本项目遵循 `0.0.x` 小步版本规则。

## 未发布

- 修复 GitHub Actions Android 任务因 `android/gradlew` 缺少可执行权限而失败的问题；
- 修复移动网关测试从仓库根目录运行时无法导入 `nexus_gateway` 的问题，CI 改为在 `gateway/` 工作目录内执行测试。

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
