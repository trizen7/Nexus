package app.nexus.mobile

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.automirrored.filled.Logout
import androidx.compose.material.icons.filled.Palette
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

@Composable
fun SettingsScreen(state: MainUiState, viewModel: MainViewModel) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    fun hasNotificationPermission(): Boolean = Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
        PackageManager.PERMISSION_GRANTED
    var notificationPermissionGranted by remember { mutableStateOf(hasNotificationPermission()) }
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) {
                notificationPermissionGranted = hasNotificationPermission()
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }
    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
    ) {
        SettingsTopBar(onBack = viewModel::closeSettings)
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(
                top = 12.dp,
                bottom = 28.dp
            ),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            item {
                SettingsSection(title = "Hermes 人格") {
                    SettingsActionRow(
                        icon = Icons.Filled.AutoAwesome,
                        title = "当前人格",
                        value = state.selectedProfileLabel,
                        onClick = viewModel::openProfilePicker
                    )
                }
            }

            item {
                SettingsSection(title = "自动化") {
                    SettingsActionRow(
                        icon = Icons.Filled.Schedule,
                        title = "定时任务",
                        value = if (state.cronJobs.isEmpty()) "暂无任务" else "${state.cronJobs.size} 个任务",
                        onClick = viewModel::openCronManager
                    )
                }
            }

            item {
                LocalAsrSettings()
            }

            item {
                SettingsSection(title = "偏好") {
                    SettingsActionRow(
                        icon = Icons.Filled.Notifications,
                        title = "通知权限",
                        value = if (notificationPermissionGranted) "已允许" else "未允许 · 点此前往系统设置",
                        onClick = {
                            context.startActivity(
                                Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).apply {
                                    putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
                                }
                            )
                        }
                    )
                    SettingsDivider()
                    SettingsSwitchRow(
                        icon = Icons.Filled.Sync,
                        title = "自动刷新消息",
                        description = "定期检查当前对话是否有新消息",
                        checked = state.autoRefresh,
                        onCheckedChange = viewModel::setAutoRefresh
                    )
                    SettingsDivider()
                    Column(Modifier.padding(horizontal = 16.dp, vertical = 14.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            SettingsIcon(Icons.Filled.Palette)
                            Spacer(Modifier.size(12.dp))
                            Column(Modifier.weight(1f)) {
                                Text("外观主题", fontWeight = FontWeight.SemiBold)
                                Text(
                                    state.themeMode.label,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    fontSize = 13.sp
                                )
                            }
                        }
                        Spacer(Modifier.height(12.dp))
                        Surface(
                            modifier = Modifier.fillMaxWidth(),
                            shape = RoundedCornerShape(8.dp),
                            color = MaterialTheme.colorScheme.background,
                            border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant)
                        ) {
                            Row(Modifier.padding(3.dp), horizontalArrangement = Arrangement.spacedBy(3.dp)) {
                                ThemeMode.entries.forEach { mode ->
                                    val selected = state.themeMode == mode
                                    Surface(
                                        modifier = Modifier
                                            .weight(1f)
                                            .clickable { viewModel.setThemeMode(mode) },
                                        shape = RoundedCornerShape(6.dp),
                                        color = if (selected) {
                                            MaterialTheme.colorScheme.primaryContainer
                                        } else {
                                            MaterialTheme.colorScheme.background
                                        }
                                    ) {
                                        Text(
                                            mode.label,
                                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 8.dp),
                                            color = if (selected) {
                                                MaterialTheme.colorScheme.onPrimaryContainer
                                            } else {
                                                MaterialTheme.colorScheme.onSurfaceVariant
                                            },
                                            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                                            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                                            maxLines = 1,
                                            fontSize = 13.sp
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }

            item {
                SettingsSection(title = "连接与版本") {
                    SettingsInfoRow("服务器", state.serverUrl)
                    SettingsDivider()
                    SettingsInfoRow("Gateway", state.gatewayVersion ?: "未知")
                    SettingsDivider()
                    SettingsInfoRow("Hermes", state.hermesVersion ?: "未知")
                    SettingsDivider()
                    SettingsInfoRow("App", BuildConfig.VERSION_NAME)
                }
            }

            item {
                SettingsSection(title = "账户") {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable(onClick = viewModel::logout)
                            .padding(horizontal = 16.dp, vertical = 14.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        SettingsIcon(
                            icon = Icons.AutoMirrored.Filled.Logout,
                            tint = MaterialTheme.colorScheme.error,
                            container = MaterialTheme.colorScheme.errorContainer
                        )
                        Spacer(Modifier.size(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(
                                "退出登录",
                                color = MaterialTheme.colorScheme.error,
                                fontWeight = FontWeight.SemiBold
                            )
                            Text(
                                "仅清除本机凭据，不会停止远端正在运行的任务",
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                fontSize = 12.sp
                            )
                        }
                    }
                }
            }

            item {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .navigationBarsPadding(),
                    horizontalArrangement = Arrangement.Center,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(
                        Icons.Filled.Info,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(15.dp)
                    )
                    Spacer(Modifier.size(6.dp))
                    Text(
                        "Nexus ${BuildConfig.VERSION_NAME}",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        fontSize = 12.sp
                    )
                }
            }
        }
    }
}

@Composable
private fun SettingsTopBar(onBack: () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        tonalElevation = 0.dp
    ) {
        Column {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .statusBarsPadding()
                    .height(56.dp)
                    .padding(horizontal = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                IconButton(onClick = onBack) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                }
                Text(
                    "设置",
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.SemiBold
                )
            }
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        }
    }
}

@Composable
private fun LocalAsrSettings() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val mainHandler = remember { Handler(Looper.getMainLooper()) }
    var installedIds by remember { mutableStateOf(LocalAsrModelManager.installedModelIds(context)) }
    val systemSpeechServices = remember(context) { SystemSpeechRecognitionManager.availableServices(context) }
    val defaultSystemSpeechLabel = remember(context, systemSpeechServices) {
        SystemSpeechRecognitionManager.defaultLabel(context, systemSpeechServices)
    }
    var selectedSystemSpeechId by remember {
        mutableStateOf(SystemSpeechRecognitionManager.selectedComponent(context)?.flattenToString())
    }
    var inlineSpeechEngine by remember { mutableStateOf(SpeechEnginePreferences.inline(context)) }
    var phoneSpeechEngine by remember { mutableStateOf(SpeechEnginePreferences.phone(context)) }
    var installingItemId by remember { mutableStateOf<String?>(null) }
    var progress by remember { mutableIntStateOf(0) }
    var error by remember { mutableStateOf<String?>(null) }
    val localModelInstalled = LOCAL_ASR_MODEL.id in installedIds

    SettingsSection(title = "语音") {
        Column {
            SpeechEngineSelector(
                title = "普通按住说话",
                selected = inlineSpeechEngine,
                xAsrInstalled = localModelInstalled,
                onSelect = { engine ->
                    SpeechEnginePreferences.selectInline(context, engine)
                    inlineSpeechEngine = engine
                },
            )
            SettingsDivider()
            SpeechEngineSelector(
                title = "电话通话",
                selected = phoneSpeechEngine,
                xAsrInstalled = localModelInstalled,
                onSelect = { engine ->
                    SpeechEnginePreferences.selectPhone(context, engine)
                    phoneSpeechEngine = engine
                },
            )
            SettingsDivider()
            Text(
                "Android 系统识别服务",
                modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 14.dp, bottom = 4.dp),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold,
            )
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable {
                        SystemSpeechRecognitionManager.select(context, null)
                        selectedSystemSpeechId = null
                    }
                    .padding(start = 8.dp, end = 16.dp, top = 8.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                RadioButton(
                    selected = selectedSystemSpeechId == null,
                    onClick = {
                        SystemSpeechRecognitionManager.select(context, null)
                        selectedSystemSpeechId = null
                    },
                )
                Column(Modifier.weight(1f)) {
                    Text("系统默认识别", fontWeight = FontWeight.Medium, fontSize = 14.sp)
                    Text(
                        "选择系统引擎时使用 · $defaultSystemSpeechLabel",
                        color = MaterialTheme.colorScheme.primary,
                        fontSize = 11.sp,
                    )
                }
            }
            systemSpeechServices.forEach { service ->
                SettingsDivider()
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable {
                            SystemSpeechRecognitionManager.select(context, service.component)
                            selectedSystemSpeechId = service.id
                        }
                        .padding(start = 8.dp, end = 16.dp, top = 8.dp, bottom = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    RadioButton(
                        selected = selectedSystemSpeechId == service.id,
                        onClick = {
                            SystemSpeechRecognitionManager.select(context, service.component)
                            selectedSystemSpeechId = service.id
                        },
                    )
                    Column(Modifier.weight(1f)) {
                        Text(service.label, fontWeight = FontWeight.Medium, fontSize = 14.sp)
                        Text(
                            service.component.packageName,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            fontSize = 11.sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
            SettingsDivider()
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SettingsIcon(Icons.Filled.GraphicEq)
                Spacer(Modifier.size(12.dp))
                Column(Modifier.weight(1f)) {
                    Text("X-ASR 模型文件", fontWeight = FontWeight.SemiBold)
                    Text(
                        if (localModelInstalled) "已安装，可供两个语音入口选择" else "未安装",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        fontSize = 12.sp,
                    )
                }
            }
            LOCAL_ASR_MODELS.forEachIndexed { index, model ->
                if (index == 0) SettingsDivider()
                val installed = model.id in installedIds
                val installing = model.id == installingItemId
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(start = 8.dp, end = 8.dp, top = 8.dp, bottom = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    SettingsIcon(Icons.Filled.GraphicEq)
                    Spacer(Modifier.size(12.dp))
                    Column(Modifier.weight(1f)) {
                        Text(model.displayName, fontWeight = FontWeight.Medium, fontSize = 14.sp)
                        Text(
                            "用于电话实时识别",
                            color = MaterialTheme.colorScheme.primary,
                            fontSize = 11.sp,
                        )
                        Text(
                            when {
                                installing -> "正在下载并安装：$progress%"
                                installed -> "${model.sizeLabel} · 已安装"
                                else -> model.sizeLabel
                            },
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            fontSize = 12.sp,
                        )
                    }
                    if (installed) {
                        IconButton(
                            onClick = {
                                if (LocalAsrModelManager.remove(context, model)) {
                                    installedIds = LocalAsrModelManager.installedModelIds(context)
                                    inlineSpeechEngine = SpeechEnginePreferences.inline(context)
                                    phoneSpeechEngine = SpeechEnginePreferences.phone(context)
                                    error = null
                                } else {
                                    error = "无法删除 ${model.displayName}"
                                }
                            },
                            enabled = installingItemId == null,
                        ) {
                            Icon(Icons.Filled.Delete, contentDescription = "删除 ${model.displayName}")
                        }
                    } else {
                        IconButton(
                            onClick = {
                                installingItemId = model.id
                                progress = 0
                                error = null
                                scope.launch {
                                    runCatching {
                                        LocalAsrModelManager.install(context, model) { value ->
                                            mainHandler.post { progress = value }
                                        }
                                    }.onSuccess {
                                        installedIds = LocalAsrModelManager.installedModelIds(context)
                                    }.onFailure {
                                        error = it.message ?: "${model.displayName} 安装失败"
                                    }
                                    installingItemId = null
                                }
                            },
                            enabled = installingItemId == null,
                        ) {
                            Icon(Icons.Filled.Download, contentDescription = "下载 ${model.displayName}")
                        }
                    }
                }
                if (installing) {
                    LinearProgressIndicator(
                        progress = { progress / 100f },
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(start = 56.dp, end = 16.dp),
                    )
                    Spacer(Modifier.height(8.dp))
                }
            }
            error?.let { message ->
                Text(
                    message,
                    modifier = Modifier.padding(start = 62.dp, end = 16.dp, bottom = 12.dp),
                    color = MaterialTheme.colorScheme.error,
                    fontSize = 12.sp,
                )
            }
        }
    }
}

@Composable
private fun SpeechEngineSelector(
    title: String,
    selected: SpeechRecognitionEngine,
    xAsrInstalled: Boolean,
    onSelect: (SpeechRecognitionEngine) -> Unit,
) {
    Column(Modifier.padding(top = 10.dp, bottom = 6.dp)) {
        Text(
            title,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
            fontWeight = FontWeight.SemiBold,
            fontSize = 14.sp,
        )
        SpeechEngineOption(
            label = "Android 系统识别",
            detail = "使用下方选择的系统语音服务",
            selected = selected == SpeechRecognitionEngine.SYSTEM,
            enabled = true,
            onClick = { onSelect(SpeechRecognitionEngine.SYSTEM) },
        )
        SpeechEngineOption(
            label = "X-ASR 本地识别",
            detail = if (xAsrInstalled) "手机端运行，不上传麦克风音频" else "需要先下载约 128 MB 模型",
            selected = selected == SpeechRecognitionEngine.X_ASR,
            enabled = xAsrInstalled,
            onClick = { onSelect(SpeechRecognitionEngine.X_ASR) },
        )
        SpeechEngineOption(
            label = "OpenAI 兼容在线识别",
            detail = "录音经 Nexus Gateway 发送到已配置的在线 ASR",
            selected = selected == SpeechRecognitionEngine.ONLINE,
            enabled = true,
            onClick = { onSelect(SpeechRecognitionEngine.ONLINE) },
        )
    }
}

@Composable
private fun SpeechEngineOption(
    label: String,
    detail: String,
    selected: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(enabled = enabled, onClick = onClick)
            .padding(start = 8.dp, end = 16.dp, top = 5.dp, bottom = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = onClick, enabled = enabled)
        Column(Modifier.weight(1f)) {
            Text(
                label,
                color = if (enabled) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                fontWeight = FontWeight.Medium,
                fontSize = 14.sp,
            )
            Text(detail, color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 11.sp)
        }
    }
}

@Composable
private fun SettingsSection(
    title: String,
    content: @Composable ColumnScope.() -> Unit
) {
    Column {
        Text(
            title,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 7.dp),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold
        )
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = MaterialTheme.colorScheme.surface
        ) {
            Column(content = content)
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
    }
}

@Composable
private fun SettingsActionRow(
    icon: ImageVector,
    title: String,
    value: String,
    onClick: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        SettingsIcon(icon)
        Spacer(Modifier.size(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Text(
                value,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontSize = 13.sp,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis
            )
        }
        Icon(
            Icons.Filled.ChevronRight,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

@Composable
private fun SettingsSwitchRow(
    icon: ImageVector,
    title: String,
    description: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { onCheckedChange(!checked) }
            .padding(horizontal = 16.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        SettingsIcon(icon)
        Spacer(Modifier.size(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Text(
                description,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontSize = 12.sp
            )
        }
        Switch(checked = checked, onCheckedChange = onCheckedChange)
    }
}

@Composable
private fun SettingsInfoRow(label: String, value: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 13.dp),
        verticalAlignment = Alignment.Top
    ) {
        Text(
            label,
            modifier = Modifier.weight(0.34f),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            fontSize = 13.sp
        )
        Text(
            value,
            modifier = Modifier.weight(0.66f),
            fontWeight = FontWeight.Medium,
            fontSize = 13.sp,
            maxLines = 3,
            overflow = TextOverflow.Ellipsis
        )
    }
}

@Composable
private fun SettingsIcon(
    icon: ImageVector,
    tint: androidx.compose.ui.graphics.Color = MaterialTheme.colorScheme.primary,
    container: androidx.compose.ui.graphics.Color = MaterialTheme.colorScheme.primaryContainer
) {
    Box(
        modifier = Modifier
            .size(34.dp)
            .background(container, RoundedCornerShape(6.dp)),
        contentAlignment = Alignment.Center
    ) {
        Icon(icon, contentDescription = null, tint = tint, modifier = Modifier.size(18.dp))
    }
}

@Composable
private fun SettingsDivider() {
    HorizontalDivider(
        modifier = Modifier.padding(start = 62.dp),
        color = MaterialTheme.colorScheme.outlineVariant
    )
}
