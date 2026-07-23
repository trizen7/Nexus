# 开发计划与待办

本文件记录 Nexus 当前开发方向与可验证待办。版本继续遵循 `0.0.x` 小步更新规则。

## 当前稳定性工作

- [x] 修复 GitHub Actions 中 Android Gradle Wrapper 执行权限；
- [x] 修复 GitHub Actions 中移动网关测试的 Python 包导入路径；
- [ ] 保持 Android 单元测试、Lint、Debug APK 构建持续通过；
- [ ] 保持网关 Python 测试、网页契约、Docker 镜像与 Compose 配置持续通过。

## 后续待办

- [ ] 补充关键 Android 界面的 UI 自动化覆盖；
- [ ] 扩充不同 Android 厂商和系统版本的真机回归记录；
- [ ] 评估正式 Release/AAB、签名与应用商店发布流程；
- [ ] 持续完善长会话、附件传输、后台回答与通知的稳定性。

## 边界

- Hermes 核心保持原版并可独立升级；
- 移动端特有的分页、缓存、附件、运行状态和断线续接放在 Android App 与 Nexus Gateway；
- Nexus Gateway 提供可选内置 HTTPS，满足本地和受控网络测试；公网域名证书、反向代理和外部访问控制仍由部署者维护。
