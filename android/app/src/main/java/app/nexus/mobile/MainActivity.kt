package app.nexus.mobile

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.core.view.WindowCompat
import androidx.core.content.ContextCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.foundation.background
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.clickable
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Keyboard
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.StopCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.Typography
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import app.nexus.mobile.network.ChatFile
import app.nexus.mobile.network.ChatImage
import app.nexus.mobile.network.ChatMessage
import app.nexus.mobile.network.ChatRole
import app.nexus.mobile.network.HermesSession

private val LightScheme = lightColorScheme(
    primary = Color(0xFF4F46E5),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFE8E7FF),
    onPrimaryContainer = Color(0xFF24205F),
    secondary = Color(0xFF0F766E),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFD8F4EF),
    onSecondaryContainer = Color(0xFF0C4A45),
    tertiary = Color(0xFF2563EB),
    background = Color(0xFFF7F7FC),
    onBackground = Color(0xFF1A1A28),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF1C1C29),
    surfaceVariant = Color(0xFFF0F1F8),
    onSurfaceVariant = Color(0xFF626575),
    outline = Color(0xFFD5D7E3),
    outlineVariant = Color(0xFFE6E7EF),
    error = Color(0xFFB42318),
    errorContainer = Color(0xFFFFE9E7),
    onErrorContainer = Color(0xFF7A271A)
)

private val DarkScheme = darkColorScheme(
    primary = Color(0xFFC4C1FF),
    onPrimary = Color(0xFF2B2674),
    primaryContainer = Color(0xFF39348B),
    onPrimaryContainer = Color(0xFFE7E5FF),
    secondary = Color(0xFF7DD3C7),
    onSecondary = Color(0xFF073D38),
    secondaryContainer = Color(0xFF155F58),
    onSecondaryContainer = Color(0xFFD0F7F1),
    tertiary = Color(0xFF9EC1FF),
    background = Color(0xFF000000),
    onBackground = Color(0xFFE7E7F0),
    surface = Color(0xFF08080C),
    onSurface = Color(0xFFE8E7F1),
    surfaceVariant = Color(0xFF14141A),
    onSurfaceVariant = Color(0xFFC7C7D4),
    outline = Color(0xFF454653),
    outlineVariant = Color(0xFF1F2028),
    error = Color(0xFFFFB4AB),
    errorContainer = Color(0xFF5F211B),
    onErrorContainer = Color(0xFFFFDAD5)
)

private val NexusTypography = Typography(
    headlineLarge = TextStyle(fontSize = 34.sp, lineHeight = 40.sp, fontWeight = FontWeight.Bold),
    headlineSmall = TextStyle(fontSize = 24.sp, lineHeight = 30.sp, fontWeight = FontWeight.Bold),
    titleLarge = TextStyle(fontSize = 20.sp, lineHeight = 26.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 16.sp, lineHeight = 22.sp, fontWeight = FontWeight.SemiBold),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 21.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 18.sp)
)

@Composable private fun appBackground() = MaterialTheme.colorScheme.background
@Composable private fun primaryInk() = MaterialTheme.colorScheme.primary
@Composable private fun userBubble() = MaterialTheme.colorScheme.surfaceVariant
@Composable private fun surfaceLine() = MaterialTheme.colorScheme.outline
@Composable private fun mutedInk() = MaterialTheme.colorScheme.onSurfaceVariant
@Composable private fun assistantSurface() = MaterialTheme.colorScheme.surface
@Composable private fun composerSurface() = MaterialTheme.colorScheme.surface
@Composable private fun errorSurface() = MaterialTheme.colorScheme.errorContainer

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels { MainViewModel.Factory(application) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        WindowCompat.getInsetsController(window, window.decorView).show(
            WindowInsetsCompat.Type.systemBars()
        )
        NotificationHelper.ensureChannels(this)
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            registerForActivityResult(ActivityResultContracts.RequestPermission()) { }.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        setContent {
            val state by viewModel.uiState.collectAsState()
            val configuration = LocalConfiguration.current
            val systemDark = (configuration.uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK) ==
                android.content.res.Configuration.UI_MODE_NIGHT_YES
            val darkTheme = shouldUseDarkTheme(state.themeMode, systemDark)
            SideEffect {
                val controller = WindowCompat.getInsetsController(window, window.decorView)
                controller.isAppearanceLightStatusBars = !darkTheme
                controller.isAppearanceLightNavigationBars = !darkTheme
            }
            MaterialTheme(
                colorScheme = if (darkTheme) DarkScheme else LightScheme,
                typography = NexusTypography
            ) {
                NexusApp(state, viewModel)
            }
        }
        handleNotificationIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleNotificationIntent(intent)
    }

    private fun handleNotificationIntent(intent: Intent?) {
        intent?.getStringExtra(NotificationHelper.EXTRA_SESSION_ID)?.let { sessionId ->
            viewModel.selectSession(sessionId)
            intent.removeExtra(NotificationHelper.EXTRA_SESSION_ID)
        }
        intent?.getStringExtra(NotificationHelper.EXTRA_FILE_KEY)?.let { fileKey ->
            viewModel.openDownloadFromNotification(fileKey)
            intent.removeExtra(NotificationHelper.EXTRA_FILE_KEY)
        }
    }
}

@Composable
private fun NexusApp(state: MainUiState, viewModel: MainViewModel) {
    BackHandler(enabled = state.settingsOpen) { viewModel.closeSettings() }
    BackHandler(enabled = state.drawerOpen && !state.settingsOpen) { viewModel.setDrawerOpen(false) }
    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner, state.connectionStatus) {
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_RESUME -> viewModel.onAppResume()
                Lifecycle.Event.ON_PAUSE -> {
                    viewModel.stopPolling()
                    viewModel.flushDrafts()
                }
                else -> Unit
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        if (state.connectionStatus == ConnectionStatus.CONNECTED &&
            lifecycleOwner.lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)
        ) {
            viewModel.onAppResume()
        }
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            viewModel.stopPolling()
        }
    }

    Surface(modifier = Modifier.fillMaxSize(), color = appBackground()) {
        when {
            state.connectionStatus != ConnectionStatus.CONNECTED -> ConnectionScreen(state, viewModel)
            state.settingsOpen -> SettingsScreen(state, viewModel)
            else -> Box(Modifier.fillMaxSize()) {
                ChatScreen(state, viewModel)
                if (state.drawerOpen) {
                    Box(
                        Modifier
                            .fillMaxSize()
                            .background(Color.Black.copy(alpha = 0.34f))
                            .clickable { viewModel.setDrawerOpen(false) }
                    )
                    SessionDrawer(
                        sessions = state.sessions,
                        activeSessionId = state.activeSessionId,
                        onSelect = viewModel::selectSession,
                        onNew = viewModel::createSession,
                        onRefresh = viewModel::refreshSessions,
                        onRename = viewModel::requestRename,
                        onDelete = viewModel::requestDelete,
                        onClose = { viewModel.setDrawerOpen(false) }
                    )
                }
            }
        }
    }

    state.sessionToRename?.let { session ->
        var title by remember(session.id) { mutableStateOf(session.displayTitle) }
        AlertDialog(
            onDismissRequest = viewModel::cancelRename,
            title = { Text("重命名对话") },
            text = {
                OutlinedTextField(
                    value = title,
                    onValueChange = { title = it },
                    label = { Text("对话名称") },
                    singleLine = true
                )
            },
            confirmButton = { TextButton(onClick = { viewModel.renameSession(title) }) { Text("保存") } },
            dismissButton = { TextButton(onClick = viewModel::cancelRename) { Text("取消") } }
        )
    }

    state.sessionToDelete?.let { session ->
        AlertDialog(
            onDismissRequest = viewModel::cancelDelete,
            title = { Text("删除对话？") },
            text = { Text("将永久删除“${session.displayTitle}”及其会话记录，此操作不能撤销。") },
            confirmButton = { TextButton(onClick = viewModel::confirmDelete) { Text("删除", color = MaterialTheme.colorScheme.error) } },
            dismissButton = { TextButton(onClick = viewModel::cancelDelete) { Text("取消") } }
        )
    }

    ManagementDialogs(state, viewModel)
    state.selectedDownload?.let { file ->
        FileDownloadDialog(file, state.downloadStates[file.id], viewModel)
    }
}

@Composable
private fun FileDownloadDialog(file: app.nexus.mobile.network.ChatFile, state: FileDownloadState?, viewModel: MainViewModel) {
    AlertDialog(
        onDismissRequest = viewModel::closeDownload,
        title = { Text(file.name, maxLines = 2) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(FileProcessor.formatSize(file.size), color = mutedInk())
                when (state?.status ?: DownloadStatus.NOT_DOWNLOADED) {
                    DownloadStatus.NOT_DOWNLOADED -> Text("文件尚未下载")
                    DownloadStatus.DOWNLOADING -> Text("正在下载 ${state?.progress ?: 0}%")
                    DownloadStatus.PAUSED -> Text("已暂停 · ${state?.progress ?: 0}%")
                    DownloadStatus.COMPLETED -> Text("下载完成，可以选择手机应用打开")
                    DownloadStatus.FAILED -> Text(state?.error ?: "下载失败", color = MaterialTheme.colorScheme.error)
                }
            }
        },
        confirmButton = {
            when (state?.status ?: DownloadStatus.NOT_DOWNLOADED) {
                DownloadStatus.NOT_DOWNLOADED, DownloadStatus.FAILED -> TextButton(onClick = viewModel::startDownload) { Text("下载") }
                DownloadStatus.DOWNLOADING -> TextButton(onClick = viewModel::pauseDownload) { Text("暂停") }
                DownloadStatus.PAUSED -> TextButton(onClick = viewModel::startDownload) { Text("继续") }
                DownloadStatus.COMPLETED -> TextButton(onClick = viewModel::openDownloadedFile) { Text("选择应用打开") }
            }
        },
        dismissButton = {
            Row {
                if (state?.status == DownloadStatus.DOWNLOADING || state?.status == DownloadStatus.PAUSED) {
                    TextButton(onClick = viewModel::cancelDownload) { Text("取消下载") }
                } else if (state?.status == DownloadStatus.COMPLETED) {
                    TextButton(onClick = viewModel::deleteDownloadedFile) { Text("删除本地文件") }
                }
                TextButton(onClick = viewModel::closeDownload) { Text("关闭") }
            }
        }
    )
}

@Composable
private fun ConnectionScreen(state: MainUiState, viewModel: MainViewModel) {
    var serverUrl by rememberSaveable { mutableStateOf(state.serverUrl) }
    var username by rememberSaveable { mutableStateOf(state.username) }
    var password by rememberSaveable { mutableStateOf(state.password) }
    var passwordVisible by rememberSaveable { mutableStateOf(false) }
    val scrollState = rememberScrollState()
    val usernameFocusRequester = remember { FocusRequester() }
    val passwordFocusRequester = remember { FocusRequester() }
    val keyboardController = LocalSoftwareKeyboardController.current
    val focusManager = LocalFocusManager.current
    val connecting = state.connectionStatus == ConnectionStatus.CONNECTING
    val canSubmit = serverUrl.isNotBlank() && username.isNotBlank() && password.isNotBlank() && !connecting

    fun clearVisibleError() {
        if (state.error != null) viewModel.clearError()
    }

    fun submit() {
        if (!canSubmit) return
        focusManager.clearFocus()
        keyboardController?.hide()
        viewModel.updateConnection(serverUrl, username, password)
        viewModel.connect()
    }

    val background = Brush.verticalGradient(
        listOf(
            MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.7f),
            MaterialTheme.colorScheme.background,
            MaterialTheme.colorScheme.background
        )
    )
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(background)
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(scrollState)
                .statusBarsPadding()
                .navigationBarsPadding()
                .imePadding()
                .padding(horizontal = 20.dp, vertical = 16.dp),
            verticalArrangement = Arrangement.Top
        ) {
            Surface(
                modifier = Modifier.size(54.dp),
                shape = RoundedCornerShape(21.dp),
                color = MaterialTheme.colorScheme.primary,
                shadowElevation = 8.dp
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Text("N", color = MaterialTheme.colorScheme.onPrimary, fontSize = 27.sp, fontWeight = FontWeight.Bold)
                }
            }
            Spacer(Modifier.height(14.dp))
            Text("欢迎使用 Nexus", style = MaterialTheme.typography.headlineLarge)
            Text(
                "连接 Hermes，随时继续你的工作",
                color = mutedInk(),
                style = MaterialTheme.typography.bodyLarge
            )
            Spacer(Modifier.height(18.dp))

            Card(
                shape = RoundedCornerShape(28.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                elevation = CardDefaults.cardElevation(defaultElevation = 4.dp)
            ) {
                Column(Modifier.padding(horizontal = 20.dp, vertical = 22.dp)) {
                    Text("登录", style = MaterialTheme.typography.titleLarge)
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "支持 HTTP 或 HTTPS，可填写 IP、域名和端口",
                        style = MaterialTheme.typography.bodySmall,
                        color = mutedInk()
                    )
                    Spacer(Modifier.height(20.dp))
                    OutlinedTextField(
                        value = serverUrl,
                        onValueChange = { serverUrl = it; clearVisibleError() },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("Nexus 地址") },
                        placeholder = { Text("http://服务器地址:18787") },
                        supportingText = { Text("例如：http://10.0.0.123:18787") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next),
                        keyboardActions = KeyboardActions(onNext = { usernameFocusRequester.requestFocus() }),
                        shape = RoundedCornerShape(16.dp)
                    )
                    Spacer(Modifier.height(10.dp))
                    OutlinedTextField(
                        value = username,
                        onValueChange = { username = it; clearVisibleError() },
                        modifier = Modifier
                            .fillMaxWidth()
                            .focusRequester(usernameFocusRequester),
                        label = { Text("账号") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                        keyboardActions = KeyboardActions(onNext = { passwordFocusRequester.requestFocus() }),
                        shape = RoundedCornerShape(16.dp)
                    )
                    Spacer(Modifier.height(10.dp))
                    OutlinedTextField(
                        value = password,
                        onValueChange = { password = it; clearVisibleError() },
                        modifier = Modifier
                            .fillMaxWidth()
                            .focusRequester(passwordFocusRequester),
                        label = { Text("密码") },
                        supportingText = {
                            Text(if (password.isEmpty()) "请输入密码" else "已输入 ${password.length} 位")
                        },
                        singleLine = true,
                        visualTransformation = if (passwordVisible) VisualTransformation.None else PasswordVisualTransformation(),
                        trailingIcon = {
                            IconButton(onClick = { passwordVisible = !passwordVisible }) {
                                Icon(
                                    if (passwordVisible) Icons.Filled.VisibilityOff else Icons.Filled.Visibility,
                                    contentDescription = if (passwordVisible) "隐藏密码" else "显示密码"
                                )
                            }
                        },
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
                        keyboardActions = KeyboardActions(onDone = { submit() }),
                        shape = RoundedCornerShape(16.dp)
                    )
                    Spacer(Modifier.height(18.dp))
                    Button(
                        onClick = { submit() },
                        enabled = canSubmit,
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(52.dp),
                        shape = RoundedCornerShape(16.dp)
                    ) {
                        if (connecting) {
                            CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                            Spacer(Modifier.width(10.dp))
                        }
                        Text(if (connecting) "连接中…" else "登录", fontWeight = FontWeight.SemiBold)
                    }
                }
            }

            state.error?.let { error ->
                Spacer(Modifier.height(14.dp))
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    color = MaterialTheme.colorScheme.errorContainer,
                    shape = RoundedCornerShape(16.dp)
                ) {
                    Text(
                        error,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 13.dp),
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        style = MaterialTheme.typography.bodyMedium
                    )
                }
            }
        }
    }
}

@OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
private fun ChatScreen(state: MainUiState, viewModel: MainViewModel) {
    Column(Modifier.fillMaxSize().imePadding()) {
        Surface(
            color = MaterialTheme.colorScheme.surface.copy(alpha = 0.98f),
            tonalElevation = 2.dp,
            shadowElevation = 1.dp
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .statusBarsPadding()
                    .height(68.dp)
                    .padding(horizontal = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                IconButton(onClick = { viewModel.setDrawerOpen(true) }) {
                    Icon(Icons.Filled.Menu, contentDescription = "打开对话列表")
                }
                Column(Modifier.weight(1f)) {
                    Text(
                        state.activeSession?.displayTitle ?: "新对话",
                        color = MaterialTheme.colorScheme.onSurface,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        fontSize = 17.sp
                    )
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            Modifier
                                .size(7.dp)
                                .background(MaterialTheme.colorScheme.secondary, RoundedCornerShape(7.dp))
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            state.answerStatus.label ?: state.selectedRuntimeSummary,
                            color = when (state.answerStatus) {
                                AnswerStatus.COMPLETED -> MaterialTheme.colorScheme.secondary
                                AnswerStatus.FAILED -> MaterialTheme.colorScheme.error
                                AnswerStatus.STOPPED -> Color(0xFF9A6B19)
                                AnswerStatus.IDLE -> mutedInk()
                                else -> primaryInk()
                            },
                            fontSize = 11.sp,
                            maxLines = 1
                        )
                        if (state.answerStatus in setOf(AnswerStatus.THINKING, AnswerStatus.TOOL, AnswerStatus.GENERATING)) {
                            Spacer(Modifier.width(6.dp))
                            CircularProgressIndicator(modifier = Modifier.size(11.dp), strokeWidth = 1.5.dp)
                        }
                    }
                }
                ChatTopAction(
                    icon = Icons.Filled.Tune,
                    label = "模型",
                    onClick = viewModel::openInferenceModelPicker
                )
                ChatTopAction(
                    icon = Icons.Filled.Psychology,
                    label = "推理",
                    onClick = viewModel::openReasoningPicker
                )
                IconButton(onClick = viewModel::openSettings) {
                    Icon(Icons.Filled.Settings, contentDescription = "设置")
                }
            }
        }

        key(state.activeSessionId) {
            val initialIndex = latestLazyListIndex(state.messages.size)
            val listState = rememberLazyListState(initialFirstVisibleItemIndex = initialIndex)
            var userHasScrolledHistory by remember { mutableStateOf(false) }
            var prependAnchorIndex by remember { mutableIntStateOf(0) }
            var prependAnchorOffset by remember { mutableIntStateOf(0) }

            LaunchedEffect(state.initialScrollToken) {
                userHasScrolledHistory = false
                if (state.messages.isNotEmpty()) listState.scrollToItem(latestLazyListIndex(state.messages.size))
            }
            val imeVisible = WindowInsets.isImeVisible
            LaunchedEffect(imeVisible) {
                if (imeVisible && state.messages.isNotEmpty()) {
                    listState.scrollToItem(latestLazyListIndex(state.messages.size))
                }
            }
            LaunchedEffect(listState) {
                snapshotFlow { listState.isScrollInProgress }.collect { scrolling ->
                    if (scrolling) userHasScrolledHistory = true
                }
            }
            LaunchedEffect(state.historyPrependToken) {
                if (state.historyPrependCount > 0) {
                    listState.scrollToItem(
                        prependAnchorLazyListIndex(prependAnchorIndex, state.historyPrependCount),
                        prependAnchorOffset
                    )
                }
            }

            val shouldRequestOlderMessages by remember(
                state.hasMoreMessages,
                state.loadingOlder,
                state.loading,
                userHasScrolledHistory
            ) {
                derivedStateOf {
                    shouldLoadOlderMessages(
                        listState.firstVisibleItemIndex,
                        state.hasMoreMessages,
                        state.loadingOlder,
                        state.loading,
                        userHasScrolledHistory
                    )
                }
            }
            LaunchedEffect(shouldRequestOlderMessages) {
                if (shouldRequestOlderMessages) {
                    prependAnchorIndex = listState.firstVisibleItemIndex
                    prependAnchorOffset = listState.firstVisibleItemScrollOffset
                    viewModel.loadOlderMessages()
                }
            }
            LaunchedEffect(state.messages.lastOrNull()?.content, state.thinking) {
                val closeToBottom = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index
                    ?.let { it >= latestLazyListIndex(state.messages.size) - 2 } != false
                if (!state.loading && state.messages.isNotEmpty() &&
                    (state.streaming || state.thinking) && closeToBottom && !state.loadingOlder
                ) {
                    listState.scrollToItem(latestLazyListIndex(state.messages.size))
                }
            }
            val visibleMessages = remember(state.messages) { state.messages.filter(::shouldRenderMessageBubble) }
            Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize().padding(horizontal = 14.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                item {
                    Box(Modifier.fillMaxWidth().height(36.dp), contentAlignment = Alignment.Center) {
                        if (state.loadingOlder) {
                            CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                        }
                    }
                }
                if (state.messages.isEmpty() && !state.loading) item { EmptyConversation() }
                items(visibleMessages, key = { it.id }) { message ->
                    MessageBubble(message, state.serverUrl, state.token, viewModel::selectDownload)
                }
                state.toolStatus?.let { status ->
                    item { Text(status, color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 13.sp, modifier = Modifier.padding(8.dp)) }
                }
                if (state.loading) {
                    item { Box(Modifier.fillMaxWidth().padding(20.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
                }
                item { Spacer(Modifier.height(8.dp)) }
            }
                val lastVisibleIndex = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0
                if (lastVisibleIndex < latestLazyListIndex(state.messages.size) - 1 && state.messages.isNotEmpty()) {
                    androidx.compose.material3.FloatingActionButton(
                        onClick = { listState.requestScrollToItem(latestLazyListIndex(state.messages.size)) },
                        modifier = Modifier.align(Alignment.BottomEnd).padding(18.dp).size(44.dp),
                        containerColor = MaterialTheme.colorScheme.surfaceVariant
                    ) {
                        Icon(Icons.Filled.KeyboardArrowDown, contentDescription = "回到最新消息")
                    }
                }
            }
        }

        state.error?.let { error ->
            Row(
                Modifier.fillMaxWidth().background(errorSurface()).padding(horizontal = 14.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(error, color = MaterialTheme.colorScheme.onErrorContainer, modifier = Modifier.weight(1f), fontSize = 12.sp)
                IconButton(onClick = viewModel::clearError, modifier = Modifier.size(36.dp)) {
                    Icon(Icons.Filled.Close, contentDescription = "关闭提示", tint = MaterialTheme.colorScheme.onErrorContainer)
                }
            }
        }

        ChatComposer(state = state, viewModel = viewModel)
    }
}

@Composable
private fun ChatTopAction(icon: ImageVector, label: String, onClick: () -> Unit) {
    IconButton(onClick = onClick) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(icon, contentDescription = label, modifier = Modifier.size(19.dp))
            Text(label, fontSize = 9.sp, lineHeight = 10.sp)
        }
    }
}

@Composable
private fun ChatComposer(state: MainUiState, viewModel: MainViewModel) {
    val context = LocalContext.current
    val keyboardController = LocalSoftwareKeyboardController.current
    val focusManager = LocalFocusManager.current
    var speechController by remember { mutableStateOf<SpeechInputController?>(null) }
    DisposableEffect(Unit) {
        onDispose {
            speechController?.cancel()
            speechController?.destroy()
        }
    }
    val systemSpeechLauncher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val text = result.data
            ?.getStringArrayListExtra(android.speech.RecognizerIntent.EXTRA_RESULTS)
            ?.firstOrNull()
        if (!text.isNullOrBlank()) viewModel.acceptVoiceResult(text)
        else viewModel.showVoiceError("没有识别到语音")
    }
    fun launchSystemSpeech() {
        runCatching {
            systemSpeechLauncher.launch(android.content.Intent(android.speech.RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE_MODEL, android.speech.RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(android.speech.RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
                putExtra(android.speech.RecognizerIntent.EXTRA_PROMPT, "请说话")
            })
        }.onFailure { viewModel.showVoiceError("当前设备没有可用的系统语音输入") }
    }
    val audioPermissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) launchSystemSpeech() else viewModel.showVoiceError("没有麦克风权限")
    }
    val galleryLauncher = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        uri?.let(viewModel::prepareImage)
    }
    val fileLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        uris.forEach { uri ->
            val persisted = runCatching {
                context.contentResolver.takePersistableUriPermission(
                    uri,
                    android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION
                )
            }.isSuccess
            viewModel.prepareFile(uri, selectedUriStorage(persisted))
        }
    }
    var cameraUri by remember { mutableStateOf<android.net.Uri?>(null) }
    val cameraLauncher = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { captured ->
        val uri = cameraUri
        if (captured && uri != null) viewModel.prepareImage(uri)
        cameraUri = null
        CameraCapture.cleanup(context)
    }
    Column(
        Modifier
            .fillMaxWidth()
            .background(composerSurface())
            .clip(RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp))
            .border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(topStart = 28.dp, topEnd = 28.dp))
            .navigationBarsPadding()
    ) {
        if (state.pendingImages.isNotEmpty() || state.preparingImage) {
            PendingImageStrip(
                state.pendingImages,
                state.preparingImage,
                state.serverUrl,
                state.token,
                viewModel::removeImage,
                viewModel::retryImageUpload
            )
        }
        if (state.pendingFiles.isNotEmpty()) {
            PendingFileStrip(
                files = state.pendingFiles,
                onRetry = viewModel::retryFileUpload,
                onRemove = viewModel::removeFile
            )
        }
        Row(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
            verticalAlignment = Alignment.Bottom
        ) {
            IconButton(onClick = {
                keyboardController?.hide()
                focusManager.clearFocus()
                viewModel.toggleVoiceInputMode()
            }) {
                Icon(
                    imageVector = if (state.voiceInputMode) Icons.Filled.Keyboard else Icons.Filled.Mic,
                    contentDescription = if (state.voiceInputMode) "切换文字输入" else "切换语音输入",
                    tint = primaryInk()
                )
            }
            if (state.voiceInputMode) {
                val voiceShape = RoundedCornerShape(24.dp)
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .height(48.dp)
                        .background(if (state.voiceListening) MaterialTheme.colorScheme.surfaceVariant else MaterialTheme.colorScheme.surface, voiceShape)
                        .border(1.dp, MaterialTheme.colorScheme.outline, voiceShape)
                        .pointerInput(Unit) {
                            detectTapGestures(
                                onPress = {
                                    if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                                        audioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                                        return@detectTapGestures
                                    }
                                    if (!isSpeechRecognitionAvailable(context)) {
                                        viewModel.showVoiceError("当前设备没有可用的语音识别服务")
                                        return@detectTapGestures
                                    }
                                    val controller = speechController ?: SpeechInputController(
                                        context,
                                        viewModel::updateVoicePreview,
                                        viewModel::acceptVoiceResult
                                    ) { code, message ->
                                        viewModel.setVoiceListening(false)
                                        if (shouldFallbackToSystemSpeech(code)) launchSystemSpeech()
                                        else viewModel.showVoiceError(message)
                                    }.also { speechController = it }
                                    viewModel.setVoiceListening(true)
                                    controller.start()
                                    tryAwaitRelease()
                                    controller.stop()
                                }
                            )
                        },
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        state.voicePreview.ifBlank { if (state.voiceListening) "松开转成文字" else "按住说话 · 转文字" },
                        color = primaryInk(),
                        fontWeight = FontWeight.Medium
                    )
                }
            } else {
                ChatTextInput(
                    modifier = Modifier.weight(1f),
                    state = state,
                    viewModel = viewModel
                )
            }
            IconButton(onClick = {
                if (!state.featurePanelOpen) {
                    keyboardController?.hide()
                    focusManager.clearFocus()
                }
                viewModel.toggleFeaturePanel()
            }) {
                Icon(
                    imageVector = Icons.Filled.Add,
                    contentDescription = "更多功能",
                    tint = primaryInk()
                )
            }
        }

        if (state.featurePanelOpen) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(260.dp)
                    .padding(horizontal = 10.dp, vertical = 24.dp),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.Top
            ) {
                FeatureEntry("文件", Icons.Filled.AttachFile) { fileLauncher.launch(arrayOf("*/*")) }
                FeatureEntry("拍照", Icons.Filled.CameraAlt) {
                    CameraCapture.createUri(context).also { uri ->
                        cameraUri = uri
                        cameraLauncher.launch(uri)
                    }
                }
                FeatureEntry("相册", Icons.Filled.Image) { galleryLauncher.launch("image/*") }
            }
        }
    }
}

@Composable
private fun ChatTextInput(
    modifier: Modifier,
    state: MainUiState,
    viewModel: MainViewModel
) {
    val input by viewModel.input.collectAsState()
    OutlinedTextField(
        value = input,
        onValueChange = viewModel::updateInput,
        modifier = modifier.onFocusChanged { focusState ->
            if (focusState.isFocused && state.featurePanelOpen) viewModel.closeFeaturePanel()
        },
        placeholder = { Text("和 Nexus 说点什么……", color = mutedInk()) },
        minLines = 1,
        maxLines = 4,
        shape = RoundedCornerShape(20.dp),
        trailingIcon = {
            if (state.streaming) {
                IconButton(onClick = viewModel::stopStreaming) {
                    Icon(Icons.Filled.StopCircle, contentDescription = "停止生成", tint = primaryInk())
                }
            } else if (canSendComposition(input, state.pendingImages.map(ChatImage::id), state.pendingFiles.map { it.id })) {
                IconButton(onClick = viewModel::send) {
                    Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "发送", tint = primaryInk())
                }
            }
        }
    )
}

@Composable
private fun PendingFileStrip(
    files: List<ChatFile>,
    onRetry: (String) -> Unit,
    onRemove: (String) -> Unit
) {
    LazyRow(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        items(files, key = ChatFile::id) { file ->
            Surface(
                modifier = Modifier.widthIn(min = 220.dp, max = 280.dp),
                shape = RoundedCornerShape(16.dp),
                color = MaterialTheme.colorScheme.surfaceVariant
            ) {
                Row(
                    modifier = Modifier.padding(start = 12.dp, top = 9.dp, bottom = 9.dp, end = 4.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(Icons.Filled.AttachFile, contentDescription = null, tint = primaryInk())
                    Spacer(Modifier.width(8.dp))
                    Column(Modifier.weight(1f)) {
                        Text(file.name, maxLines = 1, color = MaterialTheme.colorScheme.onSurface)
                        Text(
                            when (val upload = file.uploadState) {
                                AttachmentUploadState.Local -> FileProcessor.formatSize(file.size)
                                is AttachmentUploadState.Uploading -> "正在上传 ${upload.progress}%"
                                is AttachmentUploadState.Paused -> "已暂停 · ${upload.progress}%"
                                is AttachmentUploadState.Ready -> "${FileProcessor.formatSize(file.size)} · 已上传"
                                is AttachmentUploadState.Failed -> "上传失败 · 点击重试"
                            },
                            fontSize = 11.sp,
                            maxLines = 1,
                            color = if (file.uploadState is AttachmentUploadState.Failed) {
                                MaterialTheme.colorScheme.error
                            } else {
                                MaterialTheme.colorScheme.onSurfaceVariant
                            },
                            modifier = Modifier.clickable(
                                enabled = file.uploadState is AttachmentUploadState.Failed,
                                onClick = { onRetry(file.id) }
                            )
                        )
                    }
                    IconButton(onClick = { onRemove(file.id) }, modifier = Modifier.size(36.dp)) {
                        Icon(Icons.Filled.Close, contentDescription = "移除 ${file.name}", modifier = Modifier.size(18.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun PendingImageStrip(
    images: List<ChatImage>,
    preparing: Boolean,
    serverUrl: String,
    token: String,
    onRemove: (String) -> Unit,
    onRetry: (String) -> Unit
) {
    LazyRow(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        items(images, key = { it.id }) { image ->
            Box {
                AsyncImage(
                    model = if (image.previewUri.startsWith("http")) {
                        coil.request.ImageRequest.Builder(LocalContext.current)
                            .data(image.previewUri)
                            .apply {
                                bearerTokenFor(serverUrl, image.previewUri, token)?.let {
                                    addHeader("Authorization", "Bearer $it")
                                }
                            }
                            .build()
                    } else image.previewUri,
                    contentDescription = "待发送图片",
                    modifier = Modifier.size(72.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(12.dp))
                )
                when (image.uploadState) {
                    is AttachmentUploadState.Uploading -> CircularProgressIndicator(
                        modifier = Modifier.align(Alignment.Center).size(24.dp),
                        strokeWidth = 2.dp
                    )
                    is AttachmentUploadState.Failed -> Text(
                        "重试",
                        color = MaterialTheme.colorScheme.surface,
                        fontSize = 11.sp,
                        modifier = Modifier
                            .align(Alignment.BottomCenter)
                            .background(Color(0xFF9D493E), RoundedCornerShape(10.dp))
                            .clickable { onRetry(image.id) }
                            .padding(horizontal = 8.dp, vertical = 3.dp)
                    )
                    else -> Unit
                }
                Text(
                    "×",
                    color = MaterialTheme.colorScheme.surface,
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .background(Color.Black.copy(alpha = 0.55f), RoundedCornerShape(12.dp))
                        .clickable { onRemove(image.id) }
                        .padding(horizontal = 6.dp, vertical = 2.dp)
                )
            }
        }
        if (preparing) {
            item {
                Box(Modifier.size(72.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(strokeWidth = 2.dp)
                }
            }
        }
    }
}

@Composable
private fun FeatureEntry(label: String, icon: ImageVector, onClick: () -> Unit = {}) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Surface(
            onClick = onClick,
            modifier = Modifier.size(58.dp),
            shape = RoundedCornerShape(17.dp),
            color = MaterialTheme.colorScheme.surfaceVariant,
            tonalElevation = 0.dp,
            shadowElevation = 0.dp
        ) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Icon(icon, contentDescription = label, tint = primaryInk())
            }
        }
        Spacer(Modifier.height(6.dp))
        Text(label, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun EmptyConversation() {
    Column(
        modifier = Modifier.fillMaxWidth().padding(top = 72.dp, start = 24.dp, end = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Box(
            Modifier.size(54.dp).background(userBubble(), RoundedCornerShape(18.dp)),
            contentAlignment = Alignment.Center
        ) {
            Text("禾", color = primaryInk(), fontSize = 22.sp, fontWeight = FontWeight.SemiBold)
        }
        Spacer(Modifier.height(16.dp))
        Text("开始一段新的对话", fontSize = 20.sp, color = primaryInk(), fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(7.dp))
        Text(
            "文字、图片和文件都会进入当前对话，并共享Nexus的长期记忆与能力。",
            color = mutedInk(),
            fontSize = 13.sp
        )
    }
}

@Composable
private fun MessageBubble(
    message: ChatMessage,
    serverUrl: String,
    token: String,
    onFileClick: (app.nexus.mobile.network.ChatFile) -> Unit
) {
    val context = LocalContext.current
    val user = message.role == ChatRole.USER
    val downloads = if (user) emptyList() else extractDownloadableLinks(message.content)
    val visibleText = downloads.fold(message.content) { text, url -> text.replace(url, "") }
        .replace(Regex("\\n{3,}"), "\n\n")
        .trim()

    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (user) Arrangement.End else Arrangement.Start) {
        Card(
            modifier = Modifier.fillMaxWidth(if (user) 0.82f else 0.92f),
            colors = CardDefaults.cardColors(containerColor = if (user) userBubble() else assistantSurface()),
            border = if (user) null else androidx.compose.foundation.BorderStroke(1.dp, surfaceLine()),
            elevation = CardDefaults.cardElevation(defaultElevation = 0.dp),
            shape = if (user) RoundedCornerShape(18.dp, 5.dp, 18.dp, 18.dp) else RoundedCornerShape(5.dp, 18.dp, 18.dp, 18.dp)
        ) {
            Column(Modifier.padding(horizontal = 12.dp, vertical = 10.dp)) {
                if (message.images.isNotEmpty()) {
                    LazyRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        items(message.images, key = { it.id }) { image ->
                            AsyncImage(
                                model = coil.request.ImageRequest.Builder(context)
                                    .data(image.previewUri)
                                    .apply {
                                        bearerTokenFor(serverUrl, image.previewUri, token)?.let {
                                            addHeader("Authorization", "Bearer $it")
                                        }
                                    }
                                    .build(),
                                contentDescription = "聊天图片",
                                modifier = Modifier.size(150.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(12.dp))
                            )
                        }
                    }
                    if (visibleText.isNotBlank()) Spacer(Modifier.height(8.dp))
                }
                if (visibleText.isNotBlank()) {
                    MarkdownMessage(visibleText)
                }
                if (message.files.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    message.files.forEach { file ->
                        Row(
                            Modifier
                                .fillMaxWidth()
                                .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(12.dp))
                                .clickable { onFileClick(file) }
                                .padding(10.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Icon(Icons.Filled.AttachFile, contentDescription = null, tint = primaryInk())
                            Spacer(Modifier.width(8.dp))
                            Column(Modifier.weight(1f)) {
                                Text(file.name, color = primaryInk(), maxLines = 1)
                                Text("${FileProcessor.formatSize(file.size)} · 点击查看", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                        Spacer(Modifier.height(6.dp))
                    }
                }
                if (downloads.isNotEmpty()) {
                    Spacer(Modifier.height(8.dp))
                    downloads.forEach { url ->
                        val linkedFile = app.nexus.mobile.network.ChatFile(
                            id = gatewayFileId(url) ?: url,
                            name = linkedDownloadFileName(url),
                            mimeType = null,
                            size = 0,
                            uri = "",
                            downloadUrl = url
                        )
                        Row(
                            Modifier
                                .fillMaxWidth()
                                .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(12.dp))
                                .clickable { onFileClick(linkedFile) }
                                .padding(10.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Icon(Icons.Filled.AttachFile, contentDescription = null, tint = primaryInk())
                            Spacer(Modifier.width(8.dp))
                            Column(Modifier.weight(1f)) {
                                Text(linkedDownloadFileName(url), color = primaryInk(), maxLines = 1)
                                Text("点击查看文件", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                        Spacer(Modifier.height(6.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun MarkdownMessage(text: String) {
    val context = LocalContext.current
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        parseMarkdownBlocks(text).forEach { block ->
            when (block) {
                is MarkdownBlock.Heading -> SelectionContainer {
                    Text(
                        block.text,
                        color = primaryInk(),
                        fontSize = when (block.level) { 1 -> 20.sp; 2 -> 17.sp; else -> 15.sp },
                        fontWeight = FontWeight.SemiBold
                    )
                }
                is MarkdownBlock.Paragraph -> SelectionContainer {
                    Text(block.text, color = MaterialTheme.colorScheme.onSurface)
                }
                is MarkdownBlock.BulletList -> Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    block.items.forEach { item -> SelectionContainer { Text("•  $item", color = MaterialTheme.colorScheme.onSurface) } }
                }
                is MarkdownBlock.Code -> Column(
                    Modifier.fillMaxWidth().background(Color(0xFF203A3D), RoundedCornerShape(12.dp)).padding(10.dp)
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(block.language.ifBlank { "代码" }, color = Color(0xFFAFC8C4), fontSize = 11.sp, modifier = Modifier.weight(1f))
                        Text(
                            "复制",
                            color = MaterialTheme.colorScheme.surface,
                            fontSize = 12.sp,
                            modifier = Modifier.clickable {
                                context.getSystemService(android.content.ClipboardManager::class.java)
                                    .setPrimaryClip(android.content.ClipData.newPlainText("代码", block.content))
                            }.padding(4.dp)
                        )
                    }
                    SelectionContainer { Text(block.content, color = Color(0xFFE9F2F0), fontSize = 12.sp) }
                }
                is MarkdownBlock.Table -> Column(
                    Modifier.fillMaxWidth().horizontalScroll(rememberScrollState())
                ) {
                    Row { block.headers.forEach { TableCell(it, true) } }
                    block.rows.forEach { row -> Row { row.forEach { TableCell(it, false) } } }
                }
            }
        }
    }
}

@Composable
private fun TableCell(text: String, header: Boolean) {
    Text(
        text,
        modifier = Modifier
            .width(124.dp)
            .border(1.dp, surfaceLine())
            .background(if (header) MaterialTheme.colorScheme.surfaceVariant else Color.Transparent)
            .padding(8.dp),
        color = primaryInk(),
        fontSize = 12.sp,
        fontWeight = if (header) FontWeight.SemiBold else FontWeight.Normal
    )
}

@Composable
private fun SessionDrawer(
    sessions: List<HermesSession>,
    activeSessionId: String?,
    onSelect: (String) -> Unit,
    onNew: () -> Unit,
    onRefresh: () -> Unit,
    onRename: (HermesSession) -> Unit,
    onDelete: (HermesSession) -> Unit,
    onClose: () -> Unit
) {
    var search by remember { mutableStateOf("") }
    val filteredSessions = filterSessions(sessions, search)
    val groups = groupSessionsByChannel(filteredSessions)
    var expandedChannels by remember(groups.map { it.channel }, activeSessionId) {
        mutableStateOf(initialExpandedChannels(groups, activeSessionId))
    }
    var menuSessionId by remember { mutableStateOf<String?>(null) }

    Column(
        Modifier
            .fillMaxHeight()
            .fillMaxWidth(0.76f)
            .widthIn(max = 304.dp)
            .clip(RoundedCornerShape(topEnd = 28.dp, bottomEnd = 28.dp))
            .background(MaterialTheme.colorScheme.surface)
            .statusBarsPadding()
            .clickable(enabled = false) {}
            .padding(horizontal = 10.dp, vertical = 8.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("对话", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = primaryInk(), modifier = Modifier.weight(1f))
            Text("刷新", color = primaryInk(), fontSize = 13.sp, modifier = Modifier.clickable(onClick = onRefresh).padding(6.dp))
            Text("关闭", color = primaryInk(), fontSize = 13.sp, modifier = Modifier.clickable(onClick = onClose).padding(6.dp))
        }
        Spacer(Modifier.height(5.dp))
        OutlinedTextField(
            value = search,
            onValueChange = { search = it },
            modifier = Modifier.fillMaxWidth(),
            placeholder = { Text("搜索对话") },
            singleLine = true,
            shape = RoundedCornerShape(14.dp)
        )
        Spacer(Modifier.height(7.dp))
        Button(onClick = onNew, modifier = Modifier.fillMaxWidth().height(42.dp)) { Text("＋ 新建对话", fontSize = 14.sp) }
        Spacer(Modifier.height(3.dp))

        LazyColumn(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            groups.forEach { group ->
                val expanded = group.channel in expandedChannels
                item(key = "header-${group.channel.name}") {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { expandedChannels = toggleChannel(expandedChannels, group.channel) }
                            .padding(horizontal = 3.dp, vertical = 7.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(if (expanded) "▾" else "▸", color = primaryInk(), fontSize = 16.sp)
                        Spacer(Modifier.width(8.dp))
                        Text(group.channel.label, color = primaryInk(), fontSize = 14.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                        Text("${group.sessions.size}", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
                    }
                }

                if (expanded) {
                    items(group.sessions, key = { it.id }) { session ->
                        val active = session.id == activeSessionId
                        Box(Modifier.fillMaxWidth()) {
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .background(
                                        if (active) MaterialTheme.colorScheme.surfaceVariant else Color.Transparent,
                                        RoundedCornerShape(10.dp)
                                    )
                                    .clickable {
                                        menuSessionId = null
                                        onSelect(session.id)
                                    }
                                    .padding(start = 10.dp, end = 2.dp, top = 6.dp, bottom = 6.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Column(Modifier.weight(1f)) {
                                    Text(
                                        session.displayTitle,
                                        color = primaryInk(),
                                        fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal,
                                        maxLines = 1,
                                        fontSize = 14.sp
                                    )
                                    Text("${session.messageCount} 条消息", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 11.sp)
                                }
                                Text(
                                    "⋮",
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    fontSize = 22.sp,
                                    modifier = Modifier
                                        .clickable { menuSessionId = if (menuSessionId == session.id) null else session.id }
                                        .padding(horizontal = 12.dp, vertical = 4.dp)
                                )
                            }
                            if (menuSessionId == session.id) {
                                Card(
                                    modifier = Modifier.align(Alignment.CenterEnd).padding(end = 42.dp),
                                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                                    elevation = CardDefaults.cardElevation(defaultElevation = 6.dp)
                                ) {
                                    Row(Modifier.padding(horizontal = 4.dp)) {
                                        TextButton(onClick = { menuSessionId = null; onRename(session) }) { Text("重命名") }
                                        TextButton(onClick = { menuSessionId = null; onDelete(session) }) {
                                            Text("删除", color = MaterialTheme.colorScheme.error)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        Box(Modifier.fillMaxWidth().height(3.dp).background(MaterialTheme.colorScheme.tertiary, RoundedCornerShape(2.dp)))
    }
}
