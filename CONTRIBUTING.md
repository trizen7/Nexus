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
cd android
./gradlew testDebugUnitTest lintDebug assembleDebug

cd ../gateway
python -m pytest tests -q
node tests/web_contract_test.js
```

5. 提交 Pull Request，说明问题、根因、修改范围和验证结果。

本机已安装 Hermes 时，推荐使用持续本地测试环境代替手工启动 Gateway：

```bat
scripts\local-test.cmd upgrade
scripts\local-test.cmd verify
```

该流程不会调用 Docker；使用说明见 [`docs/local-test-environment.md`](docs/local-test-environment.md)。

## 代码约定

- Android 使用 Kotlin、Jetpack Compose、JDK 17；
- 网关使用 Python 3.11+ 和 aiohttp；
- 用户界面优先使用清晰的普通语言，不显示 MIME、服务器路径等技术细节；
- Hermes 核心保持原版，移动特性优先放在 App 或网关；
- 不提交生成文件、运行数据、真实配置和签名材料。

## 贡献许可

除非明确说明，你提交的贡献将按项目的 Apache License 2.0 许可。
