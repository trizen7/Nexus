# 开发计划与待办

本文件记录 Nexus 在 0.1.0 开源正式版之后的可验证工作。版本遵循语义化版本规则，稳定性与安全修复优先。

## 当前发布门禁

- [x] Gateway pytest、Python 编译和网页契约持续通过；
- [x] Android 单元测试、Lint、Debug 与 Release/AAB 编译持续通过；
- [x] 当前工作树与全部可达 Git 历史敏感信息扫描；
- [x] 官方 Android 持久签名、APK/AAB 签名校验、SHA-256 与发布清单；
- [x] Tag 驱动 GitHub Release 和 Dependabot 配置；
- [x] Hermes 原版只读边界纳入仓库规则、测试和发布流程。

## 0.1.x 稳定性工作

- [ ] 补充登录、会话、附件、定时任务和设置页的关键 UI 自动化；
- [ ] 建立不同 Android 厂商、系统版本、输入法与后台策略的真机回归矩阵；
- [ ] 增加长会话、Markdown、连续流式回答和多文件上传性能基准；
- [ ] 增加设备 Token 撤销、设备列表和本地敏感草稿加密；
- [ ] 继续完善弱网、锁屏恢复、外部渠道运行状态和通知可靠性。

## 供应链与分发

- [ ] 生成 Python 完整传递依赖锁、SBOM 和机器可读第三方许可证报告；
- [ ] 评估可验证构建和 GitHub Artifact Attestations；
- [ ] 在真机矩阵和隐私资料稳定后评估应用商店发布；
- [ ] 建立发布密钥离线备份、轮换和灾难恢复演练记录。

## 边界

- Hermes 核心保持原版、只读并由用户独立维护；
- Nexus 只通过原版 Hermes HTTP API 集成，不依赖 fork、补丁、内部数据库或私有文件布局；
- 移动端分页、缓存、附件、运行状态、通知和断线续接只放在 Android App 与 Nexus Gateway；
- Gateway 只提供 HTTP 源站；公网 HTTPS、域名、证书、HSTS 和外部访问控制由部署者的反向代理负责。
