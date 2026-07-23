# Nexus repository instructions

## Hermes 原版兼容与只读边界（强制）

Hermes 是 Nexus 的**原版、只读外部依赖**。本仓库中的任何开发、测试、构建、部署、升级和运维操作都必须遵守以下规则；如果其他说明与本节冲突，以本节为准。

1. **禁止修改任何 Hermes 文件。** 不得写入、替换、删除、移动或生成 Hermes 的源码、安装目录、虚拟环境、配置、模型路由、数据、会话、定时任务、日志、缓存、更新器及其任何其他文件。
2. **禁止管理 Hermes 安装和进程。** Nexus 的脚本、测试和工具不得安装、升级、降级、卸载、启动、停止、重启或终止 Hermes，也不得为了“恢复原状”而回滚或改写 Hermes。
3. **只允许通过原版 Hermes HTTP API 集成。** Android App 与 Nexus Gateway 不得依赖 Hermes fork、补丁、私有文件布局或内部数据库。
4. **连接信息只读。** 本地测试工具可以只读获取用户显式提供的 Hermes API 地址与 Key，或只读解析已有 Hermes 配置；只能把地址与 Key 的副本写入 Nexus 自有运行目录，绝不回写 Hermes。
5. **API 操作与文件操作严格分离。** 聊天、会话、模型查询和定时任务等产品功能可以调用 Hermes 的公开 API；相关状态是否由 Hermes 自行持久化由 Hermes 决定，Nexus 不得直接访问或修改 Hermes 的存储文件。
6. **所有 Nexus 写入必须限制在 Nexus 自有目录。** reset、upgrade、测试清理和成品部署只能处理仓库、Nexus 测试目录或 Nexus 部署目录中经过路径校验的文件。
7. **测试不得操控真实 Hermes。** 单元测试与契约测试应使用 mock/stub；端到端测试只连接已经由用户运行的原版 Hermes API，不得改变其安装、配置或进程。
8. **不得泄露敏感信息。** 禁止在输出、日志、提交、成品或文档中暴露密码、Hermes Key、Token、Session Secret、Bootstrap Token、私钥、证书内容或用户日志。

发现任何可能越过上述边界的实现时，必须停止该操作，改为仅通过原版 Hermes API 或 Nexus 自有文件完成。
