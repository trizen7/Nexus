# Nexus 发布流程

本流程用于维护者发布公开版本。所有写入都必须位于 Nexus 仓库、Nexus 成品目录或 GitHub 仓库；禁止修改、安装、更新、停止、启动、重启或管理 Hermes。发布验证不需要在本机运行 Docker。

## 1. 版本与变更记录

1. 按语义化版本更新 Android `versionCode/versionName`、Gateway `__version__`、Compose 镜像标签、README 和 Docker 契约测试；
2. 更新 `CHANGELOG.md`、中文版本记录、隐私说明和第三方依赖说明；
3. 运行：

```powershell
python scripts/scan_repository_secrets.py
python scripts/build_release.py --validate-only
```

## 2. Android 官方签名

本机签名材料只能位于被 Git 忽略的 `.release-signing/`：

```text
.release-signing/
  nexus-release.p12
  credentials.json
```

`credentials.json` 使用以下字段，文件中必须写真实值但绝不能提交或输出：

```json
{
  "store_file": "nexus-release.p12",
  "store_password": "<secret>",
  "key_alias": "nexus-release",
  "key_password": "<secret>",
  "certificate_sha256": "<64 hex characters>"
}
```

发布密钥应使用 RSA 4096、SHA256withRSA 和 PKCS12，长期离线备份。密码不得出现在命令历史、日志、截图、Issue、提交或 Release。密钥丢失会阻止已安装用户直接升级；轮换必须作为安全事件单独公告。

## 3. 本地正式构建

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-android-release.ps1
```

默认输出到 `成品/v<version>/`：

- Android Release APK；
- Android Release AAB；
- Gateway ZIP；
- `THIRD_PARTY_NOTICES.md`；
- `release-manifest.json`；
- `SHA256SUMS.txt`。

脚本会执行 Android 单元测试、Lint、APK/AAB 构建、签名验证，并确认 APK 和 AAB 使用同一证书。Gateway ZIP 时间戳固定且文件清单受控。

## 4. GitHub Actions 签名秘密

Tag 发布前，在 GitHub Actions 配置以下 repository secrets：

- `NEXUS_ANDROID_KEYSTORE_BASE64`；
- `NEXUS_RELEASE_STORE_PASSWORD`；
- `NEXUS_RELEASE_KEY_ALIAS`；
- `NEXUS_RELEASE_KEY_PASSWORD`。

Base64 只是一种传输编码，不是加密。私钥原件应保留离线备份，并限制仓库管理员与 Actions 权限。工作流只在临时 runner 目录解码签名库，日志不得打印秘密。

## 5. Git、Tag 与 Release

1. 完整门禁通过后把发布提交快进合并到 `main`；
2. 推送 `main` 并等待 CI 成功；
3. 创建带注释 Tag `v<version>`，Tag 必须指向 `main`；
4. 推送 Tag；
5. `.github/workflows/release.yml` 重新执行测试、构建签名 APK/AAB、验证签名、生成清单并创建 GitHub Release；
6. 下载 Release 附件复核 SHA-256、版本、包名、证书指纹和 Gateway ZIP 清单。

不要在签名 secrets 未配置、CI 未通过或仓库仍不可公开访问时推送正式 Tag。

## 6. GitHub 开源设置

公开发布前确认：

- 仓库 Visibility 为 Public，Issues 已启用；
- Private vulnerability reporting 已启用；
- `main` 分支保护要求 CI，通过 PR 合并并禁止强制推送/删除；
- Dependabot alerts、security updates 和 secret scanning 按仓库能力启用；
- Release 附件、README、LICENSE、NOTICE、PRIVACY 和 THIRD_PARTY_NOTICES 可匿名访问。

## 7. 回滚与修订

已发布 Tag 和签名成品不得静默替换。若发现问题，保留原 Release，说明影响并发布新的补丁版本。只有确认某附件包含敏感信息时才应立即下架附件并启动凭据轮换与安全响应。
