# 贡献指南

感谢你愿意帮助改进 Nexus。

## 提交 Bug

请先搜索现有 Issue，并提供：

- App 版本和移动网关版本；
- Android 版本、设备品牌与型号；
- 清晰的复现步骤；
- 预期结果与实际结果；
- 必要的脱敏日志或截图。

请勿提交密码、Token、私人会话、真实服务器路径或用户上传文件。

## 开发流程

1. Fork 仓库并从 `main` 创建分支；
2. 修改前先补充能复现问题的测试；
3. 保持改动集中，不在一个 PR 中混入无关重构；
4. 运行完整门禁：

```bash
python scripts/scan_repository_secrets.py

cd android
./gradlew testDebugUnitTest lintDebug assembleDebug assembleRelease

cd ../gateway
python -m pytest tests -q
python -m compileall -q nexus_gateway
node tests/web_contract_test.js
```

5. 提交 Pull Request，说明问题、根因、修改范围和验证结果。

普通 PR 的 Release 构建可以不签名；正式签名、Tag 和 GitHub Release 仅由维护者按 [`docs/releasing.md`](docs/releasing.md) 执行。

本机已安装 Hermes 时，推荐使用持续本地测试环境代替手工启动 Gateway：

```bat
scripts\local-test.cmd upgrade
scripts\local-test.cmd verify
```

该流程不会调用 Docker；使用说明见 [docs/local-test-environment.md](docs/local-test-environment.md)。

## Hermes 强制只读边界

- Hermes 必须保持原版，并作为 Nexus 的只读外部依赖；
- 禁止修改 Hermes 的任何源码、安装、虚拟环境、配置、模型路由、数据、日志、缓存或其他文件；
- 禁止由 Nexus 脚本或测试安装、更新、回滚、卸载、启动、停止、重启或终止 Hermes；
- 仅允许通过原版 Hermes HTTP API 集成；测试优先使用 mock/stub；
- 如需获取连接信息，只能只读解析，并将副本写入 Nexus 自有目录，绝不回写 Hermes；
- 聊天、会话和定时任务等正常 API 调用不等于 Nexus 直接操作 Hermes 文件。

违反此边界的提交不会被接受。完整规则见 [AGENTS.md](AGENTS.md)。

## 代码约定

- Android 使用 Kotlin、Jetpack Compose、JDK 17；
- 网关使用 Python 3.11+ 和 aiohttp；
- 用户界面优先使用清晰的普通语言，不显示 MIME、服务器路径等技术细节；
- Hermes 保持原版且只读，移动特性必须放在 App 或 Gateway，不得通过修改 Hermes 实现；
- 不提交生成文件、运行数据、真实配置和签名材料；
- 新增依赖时更新 `THIRD_PARTY_NOTICES.md`，涉及数据流或权限时同步更新 `PRIVACY.md`。

## 贡献许可

除非明确说明，你提交的贡献将按项目的 Apache License 2.0 许可。
