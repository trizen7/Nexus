# 更新记录

本项目遵循 `0.0.x` 小步版本规则。

## 未发布

- 暂无。

## 0.0.15｜Nexus 空白对话标识与历史品牌清理

- 新建对话的空白页不再显示遗留的单字人物标识，改为直接显示 Nexus 项目标识；
- 清理 README、品牌说明、部署迁移说明、历史更新记录与测试样例中的旧人物品牌信息，避免用户误以为该人物由 Nexus 固定或从 Hermes 自动读取；
- 人物模型功能保持不变：只有 Hermes API 明确返回的人物条目才会显示，未选择时继续使用 `Hermes 默认（default）`；
- 自动验证：Android 143 项单元测试通过，`lintDebug` 为 `0 errors, 1 warning`，Debug APK 构建通过；Gateway `95 passed, 9 skipped`，Python 编译与网页 JavaScript 语法检查通过；
- 版本升级到 0.0.15，并继续遵守 Hermes 原版只读边界。

## 0.0.14｜输入法即时布局与动画卡顿修复

- 删除 0.0.13 为等待输入法动画稳定而加入的 300ms 固定延迟，键盘开始显示时立即把消息区定位到最新内容；
- 登录页与聊天页不再使用 Compose `imePadding()` 跟随键盘动画逐帧改变布局；Activity 只读取系统预先分发的最终 IME 与导航栏高度，页面一次性跳到最终位置，避免长对话和 Markdown 内容在每一帧反复测量；
- 保留 `BasicTextField` 原生焦点流程、独立输入文字状态、消息解析缓存和单一尾随草稿保存任务，不重新引入手工请求焦点或强制显示输入法；
- 增加即时窗口避让策略单元测试，覆盖输入法显示、隐藏及导航栏高度兜底；
- 自动验证：Gateway `95 passed, 9 skipped`，Python 编译与网页 JavaScript 语法检查通过；Android 143 项单元测试通过，`lintDebug` 为 `0 errors, 1 warning`，Debug APK 构建通过；
- 按强制边界，本次实现、测试、构建和部署只处理 Nexus 自有文件，并继续仅通过原版 Hermes HTTP API 集成，不修改或管理任何 Hermes 文件、配置、数据、安装、虚拟环境或进程。

## 0.0.13｜输入法响应与聊天布局性能

- 修复聊天输入框在手指按下时同时执行手工请求焦点、强制显示输入法和文本框原生输入连接，造成输入法启动路径重复、弹出迟缓与动画卡顿的问题；现在完全交给 `BasicTextField` 的原生焦点流程处理；
- IME 避让从整个聊天页面收敛到底部输入区，键盘弹出期间不再立即强制重定位消息列表；待系统键盘动画稳定后再把消息区定位到最新内容，兼顾输入响应与消息随输入框上移；
- 输入框改用稳定的最小控制器参数，避免流式消息、运行状态和页面其他字段变化时无谓重组输入组件；消息中的下载链接提取与 Markdown 分块结果按内容缓存，降低键盘动画期间的主线程工作；
- 草稿保存改为单个尾随持久化任务：连续输入时只更新修订号，不再每个字符取消并重建协程；落盘时读取最新文字和附件，切换会话、发送及 App 进入后台仍会立即保存；
- 自动验证：Gateway `95 passed, 9 skipped`，Python 编译与网页 JavaScript 语法检查通过；Android 140 项单元测试通过，`lintDebug` 为 `0 errors, 1 warning`，Debug APK 构建通过；
- 按强制边界，本次实现、测试、构建和部署只处理 Nexus 自有文件，并继续仅通过原版 Hermes HTTP API 集成，不修改或管理任何 Hermes 文件、配置、数据、安装、虚拟环境或进程。

## 0.0.12｜锁屏断线续接与已提交草稿保护

- 修复消息发送后锁屏、切网或系统暂时挂起网络时，SSE 实时连接中断被误判为发送失败的问题；HTTP 响应已成功建立后发生的中断现在标记为“实时流脱离”，保留已提交的用户消息和助手占位，不显示服务器不可达或断网错误；
- App 继续通过 Nexus Gateway 的会话运行状态观察后台回答，恢复前台和网络后自动续接“思考中/生成中”状态并刷新最终消息；唤醒时的会话列表被动刷新会忽略短暂网络恢复延迟，但认证失效仍会要求重新登录；
- 用户按下发送时立即清空 Nexus 自有持久化草稿中的本次文字、图片和文件，避免锁屏或进程重建后把已发送内容重新放回输入框，同时不影响回答期间新输入的下一条草稿；
- 聊天流使用禁用自动重试的独立 HTTP 客户端，避免非幂等 POST 在连接中断时被 OkHttp 重复提交；响应头之前的真实发送失败仍按原逻辑恢复草稿并提示错误；
- 补充 SSE 中途断开、无终止事件 EOF、响应前失败、单次提交、脱离状态消息保留和草稿立即清理回归测试；
- 自动验证：Gateway 95 passed, 9 skipped，Python 编译与网页 JavaScript 检查通过；Android 140 项单元测试全部通过，lintDebug 仅保留按需求允许 HTTP 的预期警告，Debug APK 构建通过；
- 按强制边界，本次实现、测试、构建和部署只处理 Nexus 自有文件，并继续仅通过原版 Hermes HTTP API 集成，不修改或管理任何 Hermes 文件、配置、数据、安装或进程。
## 0.0.11｜Hermes 模型语义修正与外部渠道运行状态

- 修复把 Hermes `/v1/models` 中 `parent` 为空的主推理模型误识别为人物的问题；人物列表现在只接受明确标记为 `persona` 的条目，升级后清除旧版本保存的错误人物选择；
- 当前 Hermes 对外返回的主推理模型不再出现在人物列表，默认人物恢复为 `Hermes 默认（default）`，请求不发送 `persona_model`；其子路由（例如 `gpt-5.6-sol`）仍保留在调用模型列表；
- Gateway 通过原版 Hermes 的 `/health/detailed` 与 `/api/sessions` 公开 HTTP API，以只读、保守匹配方式观察 QQ、微信等其他渠道任务，使 Android App 可显示“思考中”并在完成后刷新消息；候选会话不唯一时不猜测；
- 外部渠道任务标记为不可停止，Android 只显示进度指示器；Nexus 自己发起的任务仍保留停止按钮；Hermes 状态探测失败时会清除合成状态，避免永久卡在“思考中”；
- 补充模型分类、旧人物选择迁移、运行状态兼容与外部渠道观察器回归测试；
- 自动验证：Gateway `95 passed, 9 skipped`，Python 编译与网页 JavaScript 语法检查通过；Android 135 项单元测试全部通过，`lintDebug` 为 0 errors、1 个按需求允许 HTTP 的预期警告，Debug APK 构建通过；
- 按强制边界，所有实现、测试、构建和部署只处理 Nexus 自有文件，并且仅通过原版 Hermes HTTP API 读取状态，不修改或管理任何 Hermes 文件、配置、数据、安装或进程。

## 0.0.10｜移动端附件上下文与输入体验优化

- Android 聊天请求新增 `client_context`，明确声明 Android 手机端、不可直接访问 Hermes 主机路径且不支持桌面拖拽；人物模型、调用模型、推理深度和多附件字段保持兼容；
- Gateway 消费该 Nexus 内部字段，并仅通过原版 Hermes HTTP API 把移动端约束作为本轮临时系统提示传入；原生 Session Chat 使用 `system_message`，指定调用模型时使用 OpenAI 兼容请求中的 `system` 消息，旧客户端请求保持原样；
- 手机附件增加 Android 来源语义；提示 Hermes 不得把 `/tmp/...`、`C:/...`、`sandbox:/...` 等主机路径当作已发送文件，返回内容时应直接呈现或提供手机可访问的 HTTP/HTTPS 地址，API 无法交付二进制文件时需说明限制并给出手机替代方案；
- 内部移动端提示不会显示在公开会话历史中，已有系统提示会合并而不是覆盖；
- Android 侧栏继续收窄，登录信息整体继续上移；聊天输入改为轻量输入组件并提前请求焦点和键盘，消息列表随 IME 与流式回复即时跟随，减少键盘弹出及页面位移延迟；
- 补充 Gateway 与 Android 契约测试，覆盖移动端上下文消费、两条 Hermes 路由、系统提示合并、手机附件标记、历史过滤和 Android 请求字段；
- 自动验证：Gateway `88 passed, 9 skipped`，Hermes 只读边界、本地环境与 Docker 配置契约聚焦回归 `19 passed`，Python 编译与网页契约通过；Android 133 项单元测试全部通过，`lintDebug` 为 0 errors、1 个按需求允许 HTTP 的预期警告，Debug APK 构建通过；
- 按强制边界，所有实现、测试、构建和部署均只修改 Nexus 自有文件，不修改或管理任何 Hermes 文件、配置、数据、安装或进程。

## 0.0.9｜会话级模型控制、多文件上传与 AMOLED 界面

- Android 文件选择器支持一次选择多个文件；每个文件独立上传、显示进度、失败重试和移除，发送时会把全部图片与文件 ID 一并提交；草稿可保存多文件，并兼容迁移 0.0.8 及更早版本的单文件草稿；
- 实际调用模型与推理深度改为按对话单独保存，并移动到聊天页右上角；人物模型继续作为独立的全局设置，未选择时不再固定为某个人物，而是交由原版 Hermes 使用 default；
- 设置页只保留人物模型及系统设置，模型、人物和推理深度使用三个独立选择界面；调用模型列表只显示可选择的模型名称；
- 聊天页删除右上角“新建对话”入口，保留侧栏新建对话；侧栏宽度收窄，深色主题改为 AMOLED 纯黑背景；登录内容上移，聊天页整体跟随键盘上浮，并减少流式自动滚动动画；
- 修复流式回复期间短暂显示两个助手气泡的问题；删除会话时同步清理 Nexus 自有消息缓存、草稿和会话运行配置；修复多个仅文字草稿切换后可能丢失的持久化问题；
- 重新设计 Android 自适应应用图标，提供普通、圆形和 Android 13 单色主题图标；
- 自动验证：Gateway 82 项通过、9 项按环境跳过，Hermes 只读边界与 Docker 配置契约聚焦回归 8 项通过，Python 编译与网页契约通过；Android 133 项单元测试全部通过，`lintDebug` 仅保留按需求允许明文 HTTP 的 1 项预期警告，Debug APK 构建通过；
- 按要求未在本机运行 Docker，也未修改或管理任何 Hermes 文件、安装、配置或进程。

## 0.0.8｜移动端设置、输入体验与 Web 管理页重构

- Android 新增独立全屏设置页，集中管理人物模型、实际调用模型、推理深度、定时任务、自动刷新、主题、连接信息与退出登录；模型和定时任务子页面关闭后返回设置页；
- Android 使用 Material 3 重新设计登录、聊天、会话抽屉、输入区与设置界面，统一蓝紫/青绿色视觉、圆角卡片、层级和暗色主题；
- 登录页支持滚动、状态栏/导航栏/IME 安全区与 `adjustResize`，键盘不再遮挡密码栏；密码支持显隐，并实时显示已输入位数；
- 聊天输入从整页 UI 状态中拆分，草稿磁盘保存改为 350ms 防抖并在切换会话、发送及 App 进入后台前强制落盘，减少输入法弹出和连续打字时的主线程工作；
- Android 接受显式 HTTP 或 HTTPS，裸地址默认补全 HTTP，不再拒绝公网 HTTP、不再强制 HTTPS 或显示证书提示；同源 Bearer Token 防泄漏校验继续保留；
- Gateway 健康信息在 App 中分别展示 Nexus Gateway 与 Hermes 版本；
- Web 端重构为现代化初始化与运维管理页，移除聊天、会话和消息输入界面，保留文件、语音、账号、安全和系统状态；移动端导航使用受约束的横向滚动，不再撑宽整页；Android 使用的聊天 API 保持不变；
- 建立强制 Hermes 原版只读边界：Nexus 禁止修改任何 Hermes 文件，禁止安装、升级、回滚、卸载、启动、停止或重启 Hermes，只能调用原版 HTTP API；测试环境只读获取连接信息并仅写入 Nexus 自有目录；
- 该边界自 0.0.8 起强制执行；历史版本曾对本机 Hermes 做过更新和模型路由配置，仅作为事实记录保留，后续不得复现，也不得通过再次修改 Hermes 来“恢复”；
- 自动验证：Gateway 82 项通过、9 项按环境跳过（含 5 项 Hermes 只读边界契约），Python 编译、网页契约与 JavaScript 语法检查通过；Android 126 项单元测试全部通过，`lintDebug` 仅保留按需求允许明文 HTTP 的 1 项预期警告，Debug APK 构建通过；
- 按要求未在本机运行 Docker。

## 0.0.7｜HTTP 源站与 HTTPS 反向代理

- Gateway 改回单一 HTTP 源站监听，不再生成、读取或热更新 TLS 证书，并移除临时 CA 下载与网页证书管理入口；旧 `/api/admin/tls` 路径不再转发给 Hermes；
- 外网部署改由 Nginx、Caddy 或其他反向代理提供受信任的 HTTPS 域名，文档补充 `Host`、`X-Forwarded-Proto`、流式响应关闭缓冲、长读取超时及 HSTS 边界；HTTP 源站端口不得直接暴露到公网；
- Android 允许回环、RFC 1918 私网、链路本地、CGNAT、IPv6 ULA 和 `.local` 地址使用 HTTP，拒绝公网 HTTP；裸私网地址默认补全 `http://地址:18787`，裸公网域名默认补全 `https://域名`；
- Android 自动把旧保存的私网 `https://地址:18788` 迁移为 `http://地址:18787`，移除 Debug CA 生成、内嵌和安装依赖；反向代理 HTTPS 继续使用系统信任链；
- 人物模型、实际调用模型、推理深度和手机端定时任务管理继续保留；普通对话列表继续隐藏定时任务执行会话；
- 独立成品测试环境切换到 LocalSubnet TCP `18787`，升级时安全停止旧 `18788` 进程并确认旧端口关闭；普通 upgrade 继续保留账号、Nexus 保存的 Hermes 上游连接配置副本、媒体、运行数据及历史 `data/tls`，reset 才清空数据；
- Docker、Compose、环境变量示例和源码本地测试脚本同步为 HTTP 源站；按要求未在本机运行 Docker；
- 自动验证：Gateway 77 项通过、9 项按环境跳过，Python 编译、网页契约与 JavaScript 语法检查通过；Android 126 项单元测试全部通过，`lintDebug` 与 Debug APK 构建通过；
- 已生成 `Nexus-Android-0.0.7-debug.apk` 与 `Nexus-Gateway-0.0.7.zip`，并验证 Gateway ZIP 不含运行数据、缓存、私钥、测试 CA 或 Android 构建文件；
- 独立测试环境已从 0.0.6 无损升级到 0.0.7：本机与局域网 HTTP 健康检查通过，`18787` 监听于 `0.0.0.0`、`18788` 完全关闭，账号、Nexus 保存的 Hermes 上游连接配置副本、媒体和历史 TLS 文件保持不变。

## 0.0.6｜推理深度与 HTTPS 证书管理

- Android 在人物模型和实际调用模型之外新增独立推理深度选择，支持默认、`none`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`、`ultra` 并持久保存；
- Gateway 对 `reasoning_effort` 做白名单校验并透传；Nexus 不保证 Hermes 或所选调用模型一定支持、采用该字段；
- Gateway 改为单一 HTTPS 监听，禁止 HTTP，自动生成并持久化临时 CA/服务器证书，TLS 最低版本为 1.2；当前无需提供正式证书，后续可在网页上传正式证书；
- 管理网页新增 HTTPS 证书状态与上传入口，可校验并热切换正式 PEM 证书链和未加密 PEM 私钥，失败自动回滚；
- Android 拒绝明文 HTTP，裸局域网 IP 默认补全 `https://` 与端口 `18788`；
- Android 与网页普通对话列表均隐藏 `source=cron` 的定时任务执行会话，定时任务继续通过专用管理界面操作；
- 0.0.6 Debug APK 在构建时内嵌成品测试环境 CA，手机无需手动安装证书；Release APK 仍只信任系统 CA；
- 独立成品测试环境只开放 LocalSubnet TCP `18788`，升级保留账号、Nexus 保存的 Hermes 上游连接配置副本、媒体和整个 `data/tls`，只有 reset 才删除 CA；
- Docker 与源码本地测试脚本同步改为 HTTPS-only；按要求未运行 Docker 本机测试；
- 自动验证：Gateway 87 项通过、9 项按环境跳过，Python 编译、网页契约和 JavaScript 语法检查通过；Android 126 项单元测试、`lintDebug` 与 Debug APK 构建通过；
- 已生成 `Nexus-Android-0.0.6-debug.apk` 与 `Nexus-Gateway-0.0.6.zip`，并验证 APK 内嵌 CA 与独立测试环境当前 CA 一致、Gateway ZIP 不含运行数据或私钥；
- 独立测试环境已无损升级到 0.0.6：本机与 `10.0.0.123` 的 HTTPS 健康检查通过，`18788` 正常监听、`18787` 完全关闭，账号、Nexus 保存的 Hermes 上游连接配置副本、媒体和 TLS 数据保持不变。

## 0.0.5｜Android 连接兼容与登录恢复

- 修复 Android Debug APK 连接本地 HTTPS 时不信任用户手动安装 CA 的问题；Release 构建仍只信任系统 CA；
- 连接页明确区分本地测试的 App API 端口 `18787` 与 HTTPS 网页端口 `18788`，并为遗漏协议的地址自动补全 `http://`；
- 登录前统一规范化并校验服务器地址，拒绝非 HTTP(S) 协议、账号信息、查询参数和锚点；
- 连接错误细分为账号或密码错误、设备 Token 失效、证书校验失败、DNS、拒绝连接、超时和连接中断，并提供本地 CA 下载或 HTTP 恢复路径；
- 已保存 Token 失效时自动清除无效凭据并返回登录页，用户重新输入密码后可重新签发设备 Token，不再反复使用旧 Token；
- 普通请求、停止回答和流式对话统一保留 HTTP 状态及 Gateway 错误信息，401 可可靠触发重新登录；
- 自动验证：Gateway 68 项通过、9 项按环境跳过，Python 编译和网页契约通过；Android 122 项 JVM 测试通过，Lint 0 issues，Debug APK 构建成功；
- 独立成品测试环境无损升级到 0.0.5，账号、Nexus 保存的 Hermes 上游连接配置副本、媒体和本地 HTTPS CA 保持不变，HTTP/HTTPS 健康检查与局域网双端口监听通过。

## 0.0.4｜人物与调用模型拆分、内置 HTTPS

- Android 将“人物模型”和“调用模型”拆分为两个独立选择：人物模型保留角色设定，调用模型决定实际推理模型，并支持“使用 Hermes 默认”；
- 模型列表按 Hermes `parent` 元数据分类，旧版单一模型偏好自动迁移为人物模型偏好；
- Android 发送 `persona_model` 与 `inference_model`，Gateway 继续兼容旧 `model` 字段；
- Gateway 在选择调用模型时使用 Hermes OpenAI 兼容流式接口，未选择调用模型时保留原生 Session Chat 与人物模型路由；
- Gateway 新增可选内置 HTTPS 监听、最低 TLS 1.2、网页 HTTP→HTTPS 跳转和本地 CA 下载接口；
- 独立成品测试环境使用 HTTP `18787` 提供 Android/兼容 API、HTTPS `18788` 提供网页，并持久保存本地 CA；
- 测试环境控制文件纳入 `scripts/product-test-environment/` 维护，安全同步只更新控制脚本，不修改账号、配置、媒体、虚拟环境、日志或状态；
- 验证 `gpt-5.6-sol` 调用模型路由与 Hermes 默认人物的兼容性。
- 自动验证：Gateway 68 项通过、9 项按环境跳过，Python 编译和网页契约通过；Android 116 项 JVM 测试通过，Lint 无 Error，Debug APK 构建成功；
- 独立环境完成两轮无损升级，账号、Nexus 保存的 Hermes 上游连接配置副本、媒体和 3 个 Hermes 定时任务保持不变，本地 CA 指纹在第二轮升级中保持一致；本机与局域网 HTTPS、HTTP 跳转、双端口监听和 LocalSubnet 防火墙规则均验证通过。

## 0.0.3｜移动模型选择与定时任务管理

- Android 对话列表不再显示 Hermes 定时任务执行会话；
- 手机端可读取 Hermes 模型列表、选择聊天模型并持久保存选择；
- Nexus Gateway 在指定模型时使用 Hermes OpenAI 兼容流式接口，并转换为现有移动端事件协议；未指定模型时保持原生 Session Chat 路由；
- 手机端新增独立定时任务管理页，支持列表、刷新、新建、编辑、删除、暂停、恢复和立即运行；
- 定时任务表单支持 Cron、固定间隔和 ISO 单次时间，并显示输入校验及服务器错误；
- Gateway 自动验证：65 项通过、9 项跳过；
- Android 自动验证：115 项 JVM 单元测试通过，Lint 无 Error，Debug APK 构建成功。

## 0.0.2｜安全与多会话稳定性加固

- Nexus 设备令牌仅发送到同源网关，外部图片和下载地址不再携带认证；
- HTTP 登录增加明文风险确认，并保留可信局域网 HTTP 自用能力；
- 首次初始化使用一次性 Bootstrap Token，配置或账号文件损坏时 fail closed；
- 管理员密码使用 scrypt 加盐哈希，旧明文账号可在成功登录后迁移；
- 登录增加来源 IP 限速；
- 支持多个会话同时进行本机后台状态监控，登出不停止 Hermes 服务端任务；
- 加固会话缓存、通知标识、持久 URI、SSE JSON 解析和锁屏通知隐私；
- 网关增加上传配额、磁盘低水位、绝对路径隐藏和错误脱敏；
- 修复 GitHub Actions Android Wrapper 权限与 Gateway 测试工作目录问题；
- Gradle Wrapper 增加官方 SHA-256 校验。

## 0.0.1｜首个 Nexus 开源版本

- 增加首次初始化向导：首次启动可在网页创建管理员账号，并配置 Hermes API 地址与 API Server Key；
- Docker Compose 首次部署不再要求预先创建 `.env`，配置与账号统一持久化到 `data/`；
- 兼容旧 `.env` 部署，启动时自动生成持久化 `config.json`；
- 项目统一命名为 Nexus；
- Android 应用名为 Nexus，包名为 `app.nexus.mobile`；
- 移动网关模块为 `nexus_gateway`，环境变量前缀为 `NEXUS_`；
- 提供 Android 多会话、附件、下载、通知、草稿和长会话分页能力；
- 提供 Nexus Gateway 账号认证、Hermes 会话代理、SSE、媒体和 Web 管理能力；
- 增加 Dockerfile、Docker Compose、容器健康检查、非 root 用户、持久化目录和日志轮转；
- 增加飞牛 NAS 部署、备份、更新及旧网关迁移说明；
- 增加 Android、网关、网页契约和 Docker 构建 CI；
