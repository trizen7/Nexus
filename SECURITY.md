# 安全政策

## 支持范围

当前项目处于 `0.0.x` 早期阶段，只维护最新源码和最近发布版本。

## 报告安全问题

请不要为以下问题创建公开 Issue：

- 身份验证绕过；
- Token、密码或会话密钥泄漏；
- 任意文件读取或写入；
- 上传文件导致的远程执行；
- 用户会话、媒体或服务器路径泄漏。

请通过 GitHub Security Advisory 的“Report a vulnerability”私下报告。仓库创建后应在 GitHub 设置中启用 Private vulnerability reporting。

报告中请包含影响版本、复现步骤、影响范围和建议修复方向，但不要附带真实用户数据或生产凭据。

## 部署建议

- 为移动网关使用强密码和随机会话密钥；
- Hermes API Token 只保存在网关，不放入 App；
- 公网部署必须使用 HTTPS；
- 不直接公开数据目录和上传目录；
- 定期更新依赖并备份 `gateway/data/`；
- 怀疑凭据泄漏时立即轮换密码、会话密钥和 Hermes API Token。
