# Nexus 隐私说明

生效日期：2026-08-12

Nexus 是自托管的开源 Android 客户端与移动网关，不提供由项目维护者运营的云服务。项目源码默认不包含遥测、广告、用户画像、Firebase、Sentry 或自动崩溃上报。

## 数据流向

- Android App 连接用户填写的 Nexus Gateway；
- Gateway 使用部署者配置的 Hermes API Server 地址与 Key 调用原版 Hermes HTTP API；
- 会话、模型、定时任务和回答内容是否由 Hermes 保存，由部署者使用的 Hermes 实例决定；
- Nexus 不直接读取或修改 Hermes 的内部数据库、配置或文件。

## Nexus 处理的数据

根据用户使用的功能，Nexus 可能处理：

- Gateway 地址、Nexus 管理员账号和设备登录 Token；
- 会话标识、消息、人物选择、调用模型、推理深度和定时任务请求；
- 用户主动选择或拍摄的图片、文件、语音通话临时录音和语音转写结果；
- Nexus 自有运行状态、媒体文件、配置、账号密码哈希和必要日志。

Android App 不保存 Hermes API Server Key。Gateway 会在其自有数据目录保存部署者提供的 Hermes 地址与 Key 副本，用于向该 Hermes 实例发起 API 请求。

## 设备权限

相机、文件选择、麦克风、通知和系统语音识别只在相应功能需要时使用。普通按住说话和电话通话默认使用用户选择的 Android 系统识别服务，其隐私行为不由 Nexus 控制。用户可为两个入口分别手动选择 X-ASR；选择后由手机端 sherpa-onnx 处理麦克风音频，不上传该音频，识别失败也不会自动切换到其他引擎。只有部署者以后显式配置并由用户选择 Gateway 云 ASR provider 时，通话音频才会发送到用户自己的 Nexus Gateway。

## 存储与删除

- App 数据保存在 Android 应用私有存储中，可通过 App 设置、系统“清除数据”或卸载删除；
- 语音通话的单句临时录音在转写完成或通话取消后从 App 删除；转写成功后 App 还会请求 Gateway 删除对应临时媒体；
- Gateway 数据保存在部署者指定的 Nexus 数据目录中；管理员可备份或删除该目录；
- Gateway 的 reset 会删除 Nexus 自有账号、配置、媒体和日志，但不得删除任何 Hermes 数据；
- Hermes 中的数据保留与删除应通过 Hermes 自身提供的公开功能处理。

## 网络安全

Gateway 提供 HTTP 源站。受信任局域网可以按部署者决定直接使用 HTTP；公网应通过受信任 HTTPS 反向代理访问。使用公网明文 HTTP 会暴露账号、Token、消息和附件。

## 第三方与责任边界

Nexus 依赖 Android 系统组件、部署者选择的反向代理和 Hermes 实例。部署者负责其服务器、域名、证书、备份、访问控制及适用法律要求。第三方开源组件见 `THIRD_PARTY_NOTICES.md`。

语音通话朗读由 Gateway 通过 `edge-tts` 调用 Microsoft Edge 在线语音服务。启用朗读时，待朗读的回答文本、所选语音标识以及连接所需的 IP 地址和网络元数据会发送给该外部服务；麦克风音频不会因语音合成而发送给该服务。该服务的可用性、数据处理和隐私行为由 Microsoft 控制，不属于 Nexus 自托管边界。不希望把回答文本发送给该服务时，请不要使用语音通话朗读。

## 变更

隐私说明变更会随源码提交发布。安全问题请按 `SECURITY.md` 私下报告。
