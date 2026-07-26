# Nexus 发布流程

本流程用于维护者发布版本。所有写入都必须位于 Nexus 仓库、Nexus 成品目录或 GitHub 仓库；禁止修改、安装、更新、停止、启动、重启或管理 Hermes。发布验证不需要在本机运行 Docker，仓库当前保持 Private。

## 1. 版本一致性

1. 按语义化版本更新 Android `versionCode/versionName`、Gateway `__version__`、Compose 镜像标签、README 和 Docker 契约测试。
2. 更新 `fnos/nexus-gateway/manifest` 的 fnOS 修订号，并同步本地镜像标签、测试与文档。
3. 依赖、权限或数据流发生变化时，同步更新隐私说明和第三方依赖声明。
4. 使用 Nexus 自有 Python 环境运行：

```powershell
.local-test\venv\Scripts\python.exe scripts\scan_repository_secrets.py
.local-test\venv\Scripts\python.exe scripts\build_release.py --validate-only
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

发布密钥应使用 RSA 4096、SHA256withRSA 和 PKCS12，长期离线备份。密码不得出现在命令历史、日志、截图、Issue、提交或 Release。密钥丢失会阻止已安装用户直接升级；轮换必须作为安全事件单独处理。

## 3. fnOS 自包含镜像归档

完整正式发布需要两个预先生成的 gzip Docker save 归档：

- `linux/amd64`，镜像标签为 `nexus-gateway-fnos:<Gateway 版本>`；
- `linux/arm64`，镜像标签相同。

本地发布脚本只校验并封装归档，不运行 Docker。正式归档通常由 GitHub Actions 使用 Buildx 分架构构建，并通过 `gzip --no-name --best` 压缩。归档、FPK 和任何临时载荷只能放在 Nexus 自有且被 Git 忽略的目录中，不得提交到源码树。

## 4. 本地正式构建

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-android-release.ps1 `
  -FnOSAmd64ImageArchivePath .local-test\images\nexus-gateway-amd64.tar.gz `
  -FnOSArm64ImageArchivePath .local-test\images\nexus-gateway-arm64.tar.gz
```

默认输出到 `成品/v<version>/`。完整版本只允许以下五个文件：

1. `Nexus-Android-<version>-release.apk`
2. `Nexus-Gateway-<version>.zip`
3. `Nexus-fnOS-<version>-fnos<revision>-amd64.fpk`
4. `Nexus-fnOS-<version>-fnos<revision>-arm64.fpk`
5. `SHA256SUMS.txt`

`SHA256SUMS.txt` 只包含前四个二进制附件的 SHA-256。发布目录和 GitHub Release 不生成或上传 AAB、独立 `.fpk.sha256`、`release-manifest.json`、误拼的 `renease-manifest.json`、独立第三方声明、更新记录、开发计划或 TODO。第三方许可声明只保留在源码仓库与 Gateway ZIP 内。Release 正文保持为空。

如果不提供两个 fnOS 镜像归档，脚本只构建 Android APK 与 Gateway ZIP，不能作为完整正式发布结果。

## 5. GitHub Actions 签名秘密

Tag 发布前，在 GitHub Actions 配置以下 repository secrets：

- `NEXUS_ANDROID_KEYSTORE_BASE64`
- `NEXUS_RELEASE_STORE_PASSWORD`
- `NEXUS_RELEASE_KEY_ALIAS`
- `NEXUS_RELEASE_KEY_PASSWORD`

Base64 只是一种传输编码，不是加密。私钥原件应保留离线备份，并限制仓库管理员与 Actions 权限。工作流只在临时 runner 目录解码签名库，日志不得打印秘密。

## 6. Git、Tag 与 Release

1. 完整门禁通过后将发布提交推送到 `main`。
2. 等待 CI 成功。
3. 确认目标版本尚未存在正式 GitHub Release；已发布版本不得覆盖。
4. 创建 Tag `v<version>`，Tag 必须指向 `main` 上已验证的发布提交。
5. 推送 Tag。
6. `.github/workflows/release.yml` 重新执行测试、构建签名 APK、分别构建 amd64/arm64 自包含 FPK，并创建空正文 Release。
7. 下载五个 Release 附件，复核附件数量与名称、统一 SHA-256、APK 证书指纹、FPK 架构和 Gateway ZIP 清单。

fnOS FPK 内置完整 Gateway 镜像，安装、升级和启动时不会访问 GitHub、GHCR 或 Docker Hub。工作流仍可额外发布 GHCR 多架构镜像，供普通 Docker 部署选择，但 FPK 不引用它。

## 7. 私有仓库安全设置

当前仓库保持 Private。发布前确认：

- 仓库 Visibility 仍为 Private，访问成员最小化。
- `main` 分支保护要求 CI，通过受控方式合并并禁止强制推送或删除。
- Private vulnerability reporting、Dependabot alerts、security updates 和 secret scanning 按仓库能力启用。
- Actions 只能获得发布所需最小权限，签名秘密不下发给不受信任的工作流。
- Release 附件不得包含源码外的日志、截图、运行数据、密钥、Token、证书私钥或 Hermes 配置。

## 8. 回滚与修订

已发布 Tag 和签名成品不得静默替换。若发现问题，保留原 Release 并发布新的补丁版本；只有确认附件包含敏感信息时才应立即下架并启动凭据轮换与安全响应。尚未创建正式 Release 的旧 Tag，可以在确认没有外部使用者后移动到同版本修复提交，否则必须递增版本号。
