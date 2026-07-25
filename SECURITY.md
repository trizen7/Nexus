# 安全政策

## 支持范围

当前只为最新的 `0.1.x` 正式版本和 `main` 分支提供安全修复。早期 `0.0.x` 测试版本不再单独维护。

## 私下报告安全问题

请不要为身份验证绕过、凭据泄漏、任意文件访问、上传导致的代码执行、用户会话/媒体泄漏等安全问题创建公开 Issue。请使用 GitHub Security Advisory 的 **Report a vulnerability** 私下报告。

报告中请包含受影响版本、最小复现步骤、影响范围和建议修复方向，但不要附带真实用户数据、Hermes Key、密码、Token、Session Secret、Bootstrap Token、私钥、证书内容或生产日志。

维护者会先确认收到报告，再评估严重性、准备修复和协调披露时间。修复发布前请勿公开可直接利用的细节。

## 发布物验证

- 正式 Release 提供 `SHA256SUMS.txt` 和 `release-manifest.json`；
- Android APK/AAB 由项目持久发布密钥签名，证书 SHA-256 指纹记录在发布清单中；
- 签名私钥与密码不得进入 Git、Actions 日志、Release 附件或 Gateway ZIP；
- 仓库 CI 会扫描当前工作树和所有可达 Git 历史中的常见凭据与签名材料。

## 部署建议

- 为 Nexus 管理员账号使用独立强密码；
- Hermes API Server Key 只保存在 Gateway 自有配置中，不放入 App；
- Gateway 是 HTTP 源站。公网部署必须使用受信任 HTTPS 反向代理，并限制源站端口只能由代理或可信网络访问；
- 不直接公开 `data/`、媒体目录、日志或 Bootstrap Token；
- 定期备份 Nexus 自有数据并更新依赖；
- 怀疑泄漏时立即轮换管理员密码、Session Secret、设备 Token 和 Hermes API Server Key。

## Hermes 只读边界

安全测试不得修改或管理 Hermes 的文件、配置、数据、安装、虚拟环境或进程。单元测试和契约测试使用 mock/stub；端到端测试只连接用户已经运行的原版 Hermes HTTP API。
