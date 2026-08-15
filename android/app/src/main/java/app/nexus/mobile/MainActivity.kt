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
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.content.ContextCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.waitForUpOrCancellation
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.clickable
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
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
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ChatBubbleOutline
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.EditNote
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Keyboard
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Lightbulb
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Phone
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.StopCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
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
import androidx.compose.material3.Shapes
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
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
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch


private val LightScheme = lightColorScheme(
    primary = Color(0xFF0F766E),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFCCFBF1),
    onPrimaryContainer = Color(0xFF134E4A),
    secondary = Color(0xFF2563EB),
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFDBEAFE),
    onSecondaryContainer = Color(0xFF1E3A8A),
    tertiary = Color(0xFFB45309),
    background = Color(0xFFF5F7F8),
    onBackground = Color(0xFF202326),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF202326),
    surfaceVariant = Color(0xFFEDF1F2),
    onSurfaceVariant = Color(0xFF5F686D),
    outline = Color(0xFFC7CED1),
    outlineVariant = Color(0xFFE0E5E7),
    error = Color(0xFFB42318),
    errorContainer = Color(0xFFFFE9E7),
    onErrorContainer = Color(0xFF7A271A)
)

private val DarkScheme = darkColorScheme(
    primary = Color(0xFF5EEAD4),
    onPrimary = Color(0xFF083B37),
    primaryContainer = Color(0xFF134E4A),
    onPrimaryContainer = Color(0xFFCCFBF1),
    secondary = Color(0xFF93C5FD),
    onSecondary = Color(0xFF172554),
    secondaryContainer = Color(0xFF1E3A5F),
    onSecondaryContainer = Color(0xFFDBEAFE),
    tertiary = Color(0xFFFBBF24),
    background = Color(0xFF111416),
    onBackground = Color(0xFFE5E9EA),
    surface = Color(0xFF181C1E),
    onSurface = Color(0xFFE5E9EA),
    surfaceVariant = Color(0xFF252A2D),
    onSurfaceVariant = Color(0xFFB8C0C4),
    outline = Color(0xFF465055),
    outlineVariant = Color(0xFF2D3437),
    error = Color(0xFFFFB4AB),
    errorContainer = Color(0xFF5F211B),
    onErrorContainer = Color(0xFFFFDAD5)
)

private val NexusTypography = Typography(
    headlineLarge = TextStyle(fontSize = 28.sp, lineHeight = 34.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.sp),
    headlineSmall = TextStyle(fontSize = 22.sp, lineHeight = 28.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.sp),
    titleLarge = TextStyle(fontSize = 19.sp, lineHeight = 25.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.sp),
    titleMedium = TextStyle(fontSize = 15.sp, lineHeight = 21.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.sp),
    bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 23.sp, letterSpacing = 0.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 21.sp, letterSpacing = 0.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 18.sp, letterSpacing = 0.sp)
)

private val NexusShapes = Shapes(
    extraSmall = RoundedCornerShape(4.dp),
    small = RoundedCornerShape(6.dp),
    medium = RoundedCornerShape(8.dp),
    large = RoundedCornerShape(8.dp),
    extraLarge = RoundedCornerShape(8.dp),
)

@Composable private fun appBackground() = MaterialTheme.colorScheme.background
@Composable private fun primaryInk() = MaterialTheme.colorScheme.primary
@Composable private fun surfaceLine() = MaterialTheme.colorScheme.outline
@Composable private fun mutedInk() = MaterialTheme.colorScheme.onSurfaceVariant
@Composable private fun composerSurface() = MaterialTheme.colorScheme.surface
@Composable private fun errorSurface() = MaterialTheme.colorScheme.errorContainer

class MainActivity : ComponentActivity() {
    private val viewModel: MainViewModel by viewModels { MainViewModel.Factory(application) }
    private var instantWindowInsets by mutableStateOf(InstantWindowInsets())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        lifecycleScope.launch {
            runCatching { LocalAsrModelManager.cleanupRetiredModels(this@MainActivity) }
        }
        enableEdgeToEdge()
        ViewCompat.setOnApplyWindowInsetsListener(window.decorView) { _, insets ->
            val imeInsets = insets.getInsets(WindowInsetsCompat.Type.ime())
            val navigationInsets = insets.getInsets(WindowInsetsCompat.Type.navigationBars())
            val updatedInsets = resolveInstantWindowInsets(
                imeVisible = insets.isVisible(WindowInsetsCompat.Type.ime()),
                imeBottomPx = imeInsets.bottom,
                navigationBottomPx = navigationInsets.bottom
            )
            if (instantWindowInsets != updatedInsets) instantWindowInsets = updatedInsets
            insets
        }
        ViewCompat.requestApplyInsets(window.decorView)
        WindowCompat.getInsetsController(window, window.decorView).show(
            WindowInsetsCompat.Type.systemBars()
        )
        NotificationHelper.ensureChannels(this)
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
                typography = NexusTypography,
                shapes = NexusShapes,
            ) {
                NexusApp(state, viewModel, instantWindowInsets)
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
        val profileId = intent?.getStringExtra(NotificationHelper.EXTRA_PROFILE_ID)
        val sessionId = intent?.getStringExtra(NotificationHelper.EXTRA_SESSION_ID)
        val fileKey = intent?.getStringExtra(NotificationHelper.EXTRA_FILE_KEY)
        val voiceCall = intent?.getBooleanExtra(NotificationHelper.EXTRA_VOICE_CALL, false) == true
        if (voiceCall) {
            viewModel.openVoiceCall()
        } else if (sessionId != null) {
            viewModel.openNotificationSession(profileId, sessionId, fileKey)
        } else {
            viewModel.openNotificationProfile(profileId)
            fileKey?.let(viewModel::openDownloadFromNotification)
        }
        intent?.removeExtra(NotificationHelper.EXTRA_SESSION_ID)
        intent?.removeExtra(NotificationHelper.EXTRA_PROFILE_ID)
        intent?.removeExtra(NotificationHelper.EXTRA_FILE_KEY)
        intent?.removeExtra(NotificationHelper.EXTRA_VOICE_CALL)
    }
}

@Composable
private fun NexusApp(state: MainUiState, viewModel: MainViewModel, windowInsets: InstantWindowInsets) {
    val context = LocalContext.current
    val notificationPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { }
    LaunchedEffect(state.connectionStatus) {
        if (android.os.Build.VERSION.SDK_INT < 33 || state.connectionStatus != ConnectionStatus.CONNECTED) {
            return@LaunchedEffect
        }
        val permissionPreferences = context.getSharedPreferences("nexus_permissions", android.content.Context.MODE_PRIVATE)
        val alreadyRequested = permissionPreferences.getBoolean("notification_permission_requested", false)
        val permissionGranted = ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
        if (!alreadyRequested && !permissionGranted) {
            permissionPreferences.edit().putBoolean("notification_permission_requested", true).apply()
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }
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
        Box(Modifier.fillMaxSize()) {
            when {
                state.connectionStatus != ConnectionStatus.CONNECTED -> ConnectionScreen(state, viewModel, windowInsets)
                state.settingsOpen -> SettingsScreen(state, viewModel)
                else -> Box(Modifier.fillMaxSize()) {
                    ChatScreen(state, viewModel, windowInsets)
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
            if (state.connectionStatus == ConnectionStatus.CONNECTED && state.voiceCallOpen) {
                VoiceCallScreen(state, viewModel)
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
private fun ConnectionScreen(state: MainUiState, viewModel: MainViewModel, windowInsets: InstantWindowInsets) {
    var serverUrl by rememberSaveable { mutableStateOf(state.serverUrl) }
    var username by rememberSaveable { mutableStateOf(state.username) }
    var password by rememberSaveable { mutableStateOf(state.password) }
    var passwordVisible by rememberSaveable { mutableStateOf(false) }
    val scrollState = rememberScrollState()
    val usernameFocusRequester = remember { FocusRequester() }
    val passwordFocusRequester = remember { FocusRequester() }
    val keyboardController = LocalSoftwareKeyboardController.current
    val focusManager = LocalFocusManager.current
    val bottomInset = with(LocalDensity.current) { windowInsets.bottomPx.toDp() }
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

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background),
        contentAlignment = Alignment.TopCenter,
    ) {
        Column(
            modifier = Modifier
                .widthIn(max = 440.dp)
                .fillMaxWidth()
                .verticalScroll(scrollState)
                .statusBarsPadding()
                .padding(bottom = bottomInset)
                .padding(horizontal = 24.dp, vertical = 24.dp),
            verticalArrangement = Arrangement.Top
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Surface(
                    modifier = Modifier.size(40.dp),
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.primary,
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Text("N", color = MaterialTheme.colorScheme.onPrimary, fontSize = 20.sp, fontWeight = FontWeight.Bold)
                    }
                }
                Spacer(Modifier.width(12.dp))
                Column {
                    Text("Nexus", style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.onSurface)
                    Text("移动工作台", color = mutedInk(), style = MaterialTheme.typography.bodySmall)
                }
            }
            Spacer(Modifier.height(40.dp))
            Text("连接到工作区", style = MaterialTheme.typography.headlineSmall, color = MaterialTheme.colorScheme.onSurface)
            Spacer(Modifier.height(6.dp))
            Text("使用 Nexus Gateway 地址和账户登录", color = mutedInk(), style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.height(24.dp))
            OutlinedTextField(
                value = serverUrl,
                onValueChange = { serverUrl = it; clearVisibleError() },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Gateway 地址") },
                placeholder = { Text("http://服务器地址:端口") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next),
                keyboardActions = KeyboardActions(onNext = { usernameFocusRequester.requestFocus() }),
                shape = RoundedCornerShape(8.dp),
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = username,
                onValueChange = { username = it; clearVisibleError() },
                modifier = Modifier.fillMaxWidth().focusRequester(usernameFocusRequester),
                label = { Text("账号") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
                keyboardActions = KeyboardActions(onNext = { passwordFocusRequester.requestFocus() }),
                shape = RoundedCornerShape(8.dp),
            )
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(
                value = password,
                onValueChange = { password = it; clearVisibleError() },
                modifier = Modifier.fillMaxWidth().focusRequester(passwordFocusRequester),
                label = { Text("密码") },
                singleLine = true,
                visualTransformation = if (passwordVisible) VisualTransformation.None else PasswordVisualTransformation(),
                trailingIcon = {
                    IconButton(onClick = { passwordVisible = !passwordVisible }) {
                        Icon(
                            if (passwordVisible) Icons.Filled.VisibilityOff else Icons.Filled.Visibility,
                            contentDescription = if (passwordVisible) "隐藏密码" else "显示密码",
                        )
                    }
                },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { submit() }),
                shape = RoundedCornerShape(8.dp),
            )
            Spacer(Modifier.height(18.dp))
            Button(
                onClick = { submit() },
                enabled = canSubmit,
                modifier = Modifier.fillMaxWidth().height(48.dp),
                shape = RoundedCornerShape(8.dp),
            ) {
                if (connecting) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(10.dp))
                }
                Text(if (connecting) "连接中…" else "登录", fontWeight = FontWeight.SemiBold)
            }

            state.error?.let { error ->
                Spacer(Modifier.height(12.dp))
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    color = MaterialTheme.colorScheme.errorContainer,
                    shape = RoundedCornerShape(8.dp),
                ) {
                    Text(
                        error,
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 13.dp),
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        style = MaterialTheme.typography.bodyMedium
                    )
                }
            }
            Spacer(Modifier.height(28.dp))
            Text(
                "Nexus ${BuildConfig.VERSION_NAME}",
                modifier = Modifier.fillMaxWidth(),
                color = mutedInk(),
                fontSize = 12.sp,
                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
            )
        }
    }
}

@Composable
private fun ChatScreen(state: MainUiState, viewModel: MainViewModel, windowInsets: InstantWindowInsets) {
    Column(Modifier.fillMaxSize()) {
        Surface(
            color = MaterialTheme.colorScheme.surface,
            tonalElevation = 0.dp,
            shadowElevation = 0.dp
        ) {
            Column(Modifier.fillMaxWidth().statusBarsPadding()) {
                Row(
                    modifier = Modifier.fillMaxWidth().height(56.dp).padding(horizontal = 8.dp),
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
                            fontSize = 16.sp,
                        )
                        Text(
                            state.selectedProfileLabel,
                            color = mutedInk(),
                            maxLines = 1,
                            fontSize = 11.sp,
                        )
                    }
                    IconButton(onClick = viewModel::openVoiceCall) {
                        Icon(Icons.Filled.Phone, contentDescription = "语音通话", modifier = Modifier.size(20.dp), tint = mutedInk())
                    }
                    IconButton(onClick = viewModel::openSettings) {
                        Icon(Icons.Filled.Settings, contentDescription = "设置", modifier = Modifier.size(20.dp), tint = mutedInk())
                    }
                }
            }
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)

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
            LaunchedEffect(windowInsets.imeVisible) {
                if (windowInsets.imeVisible && state.messages.isNotEmpty()) {
                    val lastItemIndex = (listState.layoutInfo.totalItemsCount - 1).coerceAtLeast(0)
                    listState.requestScrollToItem(lastItemIndex)
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
                state.activeSessionId,
                state.messages.size,
                state.hasMoreMessages,
                state.loadingOlder,
                state.loading,
                userHasScrolledHistory
            ) {
                derivedStateOf {
                    shouldLoadOlderMessages(
                        firstVisibleItemIndex = listState.firstVisibleItemIndex,
                        activeSessionId = state.activeSessionId,
                        messageCount = state.messages.size,
                        hasMoreMessages = state.hasMoreMessages,
                        loadingOlder = state.loadingOlder,
                        loading = state.loading,
                        userHasScrolled = userHasScrolledHistory
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
                    listState.requestScrollToItem(latestLazyListIndex(state.messages.size))
                }
            }
            val visibleMessages = remember(state.messages) { state.messages.filter(::shouldRenderMessageBubble) }
            Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
            LazyColumn(
                state = listState,
                modifier = Modifier.fillMaxSize().padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                item {
                    Box(Modifier.fillMaxWidth().height(36.dp), contentAlignment = Alignment.Center) {
                        if (state.loadingOlder) {
                            CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp)
                        }
                    }
                }
                if (state.messages.isEmpty() && !state.loading) item {
                    EmptyConversation(onPromptSelected = viewModel::updateInput)
                }
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

        ChatComposer(state = state, viewModel = viewModel, windowInsets = windowInsets)
    }
}

@Composable
private fun ChatComposer(state: MainUiState, viewModel: MainViewModel, windowInsets: InstantWindowInsets) {
    val context = LocalContext.current
    val bottomInset = with(LocalDensity.current) { windowInsets.bottomPx.toDp() }
    val keyboardController = LocalSoftwareKeyboardController.current
    val focusManager = LocalFocusManager.current
    val inputController = remember(viewModel) {
        ChatInputController(
            input = viewModel.input,
            onInputChanged = viewModel::updateInput,
            onCloseFeaturePanel = viewModel::closeFeaturePanel,
            onStopStreaming = viewModel::stopStreaming,
            onSend = viewModel::send
        )
    }
    val systemSpeechAvailable = remember(context) { isSpeechRecognitionAvailable(context) }
    val inlineSpeechEngine = remember(state.voiceInputMode) { SpeechEnginePreferences.inline(context) }
    val localSpeechFiles = remember(state.voiceInputMode) { LocalAsrModelManager.selectedModelFiles(context) }
    val inlineTranscriptionScope = rememberCoroutineScope()
    val currentVoiceTranscribing = rememberUpdatedState(state.voiceTranscribing)
    val currentVoiceListening = rememberUpdatedState(state.voiceListening)
    val inlineSpeechController = remember(
        context,
        viewModel,
        systemSpeechAvailable,
        inlineSpeechEngine,
        localSpeechFiles,
    ) {
        when (inlineSpeechEngine) {
            SpeechRecognitionEngine.SYSTEM -> if (systemSpeechAvailable) SpeechInputController(
                context = context,
                onPartial = viewModel::updateVoicePreview,
                onResult = { text, recording ->
                    recording?.file?.delete()
                    viewModel.acceptVoiceResult(text)
                },
                onError = { _, message, recording ->
                    recording?.file?.delete()
                    viewModel.showVoiceError(message)
                },
                onReady = { viewModel.setVoiceListening(true) },
                onSpeechStart = { viewModel.setVoiceListening(true) },
                onSpeechEnd = viewModel::setVoiceTranscribing,
                captureEchoCanceledAudio = false,
                recognitionService = SystemSpeechRecognitionManager.selectedComponent(context),
            ) else null
            SpeechRecognitionEngine.X_ASR -> if (localSpeechFiles != null) LocalStreamingSpeechController(
                context = context,
                onPartial = { _, text -> viewModel.updateVoicePreview(text) },
                onResult = { _, text, recording ->
                    recording?.file?.delete()
                    viewModel.acceptVoiceResult(text)
                },
                onError = { _, message, recording ->
                    recording?.file?.delete()
                    viewModel.showVoiceError(message)
                },
                onReady = { viewModel.setVoiceListening(true) },
                onSpeechStart = { viewModel.setVoiceListening(true) },
                onSpeechEnd = { viewModel.setVoiceTranscribing() },
                onSpeechCanceled = { viewModel.showVoiceError("没有识别到语音") },
                continuous = false,
            ) else null
            SpeechRecognitionEngine.ONLINE -> OnlineSpeechInputController(
                context = context,
                onResult = { recording ->
                    viewModel.setVoiceTranscribing()
                    inlineTranscriptionScope.launch {
                        runCatching { viewModel.transcribeVoice(recording, deleteRemoteAfterTranscription = true) }
                            .onSuccess(viewModel::acceptVoiceResult)
                            .onFailure { viewModel.showVoiceError("在线语音识别失败，请检查 Gateway 的 ASR 配置") }
                    }
                },
                onError = { _, message, recording ->
                    recording?.file?.delete()
                    viewModel.showVoiceError(message)
                },
                onReady = { viewModel.setVoiceListening(true) },
                onSpeechStart = { viewModel.setVoiceListening(true) },
                onSpeechEnd = viewModel::setVoiceTranscribing,
            )
        }
    }
    DisposableEffect(inlineSpeechController) {
        onDispose { inlineSpeechController?.destroy() }
    }
    LaunchedEffect(state.voiceInputMode) {
        if (!state.voiceInputMode) inlineSpeechController?.cancel()
    }
    fun startInlineSpeech(): Boolean {
        val controller = inlineSpeechController
        if (controller == null) {
            viewModel.showVoiceError(
                if (inlineSpeechEngine == SpeechRecognitionEngine.X_ASR) {
                    "请先在设置中安装 X-ASR"
                } else if (inlineSpeechEngine == SpeechRecognitionEngine.ONLINE) {
                    "请先在 Gateway 管理页配置 OpenAI 兼容在线 ASR"
                } else {
                    "当前设备没有可用的系统语音输入"
                }
            )
            return false
        }
        return runCatching {
            viewModel.setVoiceListening(true)
            controller.start()
        }.onFailure {
            viewModel.showVoiceError("无法启动语音识别")
        }.isSuccess
    }
    val audioPermissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) viewModel.updateVoicePreview("请按住说话")
        else viewModel.showVoiceError("没有麦克风权限")
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
            .background(MaterialTheme.colorScheme.surface)
            .padding(bottom = bottomInset)
            .padding(horizontal = 10.dp, vertical = 8.dp)
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
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = composerSurface(),
            shape = RoundedCornerShape(8.dp),
            border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
            shadowElevation = 0.dp
        ) {
        Row(
            modifier = Modifier.padding(horizontal = 6.dp, vertical = 6.dp),
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
                val voiceShape = RoundedCornerShape(6.dp)
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .height(48.dp)
                        .background(if (state.voiceListening) MaterialTheme.colorScheme.surfaceVariant else MaterialTheme.colorScheme.surface, voiceShape)
                        .border(1.dp, MaterialTheme.colorScheme.outline, voiceShape)
                        .pointerInput(inlineSpeechController) {
                            awaitEachGesture {
                                val down = awaitFirstDown(requireUnconsumed = false)
                                var started = false
                                if (!currentVoiceTranscribing.value) {
                                    down.consume()
                                    if (
                                        ContextCompat.checkSelfPermission(
                                            context,
                                            Manifest.permission.RECORD_AUDIO,
                                        ) == PackageManager.PERMISSION_GRANTED
                                    ) {
                                        started = startInlineSpeech()
                                    } else {
                                        audioPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                                    }
                                }
                                val released = waitForUpOrCancellation()
                                if (started) {
                                    if (released == null) {
                                        inlineSpeechController?.cancel()
                                        viewModel.setVoiceListening(false)
                                        viewModel.updateVoicePreview("")
                                    } else if (
                                        shouldFinishInlineSpeechOnRelease(
                                            started = started,
                                            stillListening = currentVoiceListening.value,
                                        )
                                    ) {
                                        viewModel.setVoiceTranscribing()
                                        inlineSpeechController?.stop()
                                    }
                                }
                            }
                        },
                    contentAlignment = Alignment.Center
                ) {
                    Text(
                        state.voicePreview.ifBlank { if (state.voiceListening) "松开发送" else "按住说话" },
                        color = primaryInk(),
                        fontWeight = FontWeight.Medium
                    )
                }
            } else {
                ChatTextInput(
                    modifier = Modifier.weight(1f),
                    controller = inputController,
                    featurePanelOpen = state.featurePanelOpen,
                    streaming = state.streaming,
                    thinking = state.thinking,
                    runStoppable = state.runStoppable,
                    hasPendingAttachments = state.pendingImages.isNotEmpty() || state.pendingFiles.isNotEmpty()
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
        }

        if (state.featurePanelOpen) {
            LazyRow(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(100.dp),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 12.dp, vertical = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(18.dp),
                verticalAlignment = Alignment.Top,
            ) {
                item("model") {
                    FeatureEntry("模型", Icons.Filled.SmartToy) {
                        viewModel.closeFeaturePanel()
                        viewModel.openInferenceModelPicker()
                    }
                }
                item("reasoning") {
                    FeatureEntry("推理", Icons.Filled.Psychology) {
                        viewModel.closeFeaturePanel()
                        viewModel.openReasoningPicker()
                    }
                }
                item("file") {
                    FeatureEntry("文件", Icons.Filled.AttachFile) { fileLauncher.launch(arrayOf("*/*")) }
                }
                item("camera") {
                    FeatureEntry("拍照", Icons.Filled.CameraAlt) {
                        CameraCapture.createUri(context).also { uri ->
                            cameraUri = uri
                            cameraLauncher.launch(uri)
                        }
                    }
                }
                item("gallery") {
                    FeatureEntry("相册", Icons.Filled.Image) { galleryLauncher.launch("image/*") }
                }
            }
        }
    }
}

@Stable
private class ChatInputController(
    val input: StateFlow<String>,
    val onInputChanged: (String) -> Unit,
    val onCloseFeaturePanel: () -> Unit,
    val onStopStreaming: () -> Unit,
    val onSend: () -> Unit
)

@Composable
private fun ChatTextInput(
    modifier: Modifier,
    controller: ChatInputController,
    featurePanelOpen: Boolean,
    streaming: Boolean,
    thinking: Boolean,
    runStoppable: Boolean,
    hasPendingAttachments: Boolean
) {
    val input by controller.input.collectAsState()
    var focused by remember { mutableStateOf(false) }
    var activityDots by remember { mutableIntStateOf(1) }
    val shape = RoundedCornerShape(6.dp)
    val cursorColor = MaterialTheme.colorScheme.primary
    val fieldColor = if (focused) MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.78f) else Color.Transparent

    LaunchedEffect(streaming) {
        if (!streaming) {
            activityDots = 1
            return@LaunchedEffect
        }
        while (true) {
            delay(360L)
            activityDots = activityDots % 3 + 1
        }
    }

    BasicTextField(
        value = input,
        onValueChange = controller.onInputChanged,
        modifier = modifier.onFocusChanged { focusState ->
            focused = focusState.isFocused
            if (focusState.isFocused && featurePanelOpen) controller.onCloseFeaturePanel()
        },
        textStyle = MaterialTheme.typography.bodyLarge.copy(color = MaterialTheme.colorScheme.onSurface),
        cursorBrush = SolidColor(cursorColor),
        minLines = 1,
        maxLines = 4,
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Default),
        decorationBox = { innerTextField ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 48.dp)
                    .background(fieldColor, shape)
                    .padding(start = 14.dp, end = 2.dp),
                verticalAlignment = Alignment.Bottom
            ) {
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .padding(vertical = 13.dp)
                ) {
                    if (input.isEmpty()) {
                        Text(
                            if (streaming) {
                                "${if (thinking) "思考中" else "回答中"}${".".repeat(activityDots)}"
                            } else {
                                "发消息或描述一个任务…"
                            },
                            color = if (streaming) MaterialTheme.colorScheme.primary else mutedInk(),
                            style = MaterialTheme.typography.bodyLarge
                        )
                    }
                    innerTextField()
                }
                if (streaming && runStoppable) {
                    IconButton(onClick = controller.onStopStreaming) {
                        Icon(Icons.Filled.StopCircle, contentDescription = "停止生成", tint = primaryInk())
                    }
                } else if (streaming) {
                    Spacer(Modifier.size(48.dp))
                } else if (input.isNotBlank() || hasPendingAttachments) {
                    IconButton(onClick = controller.onSend) {
                        Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "发送", tint = primaryInk())
                    }
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
                shape = RoundedCornerShape(8.dp),
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
                    modifier = Modifier.size(72.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(8.dp))
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
                            .background(MaterialTheme.colorScheme.error, RoundedCornerShape(6.dp))
                            .clickable { onRetry(image.id) }
                            .padding(horizontal = 8.dp, vertical = 3.dp)
                    )
                    else -> Unit
                }
                Icon(
                    Icons.Filled.Close,
                    contentDescription = "移除图片",
                    tint = Color.White,
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .size(23.dp)
                        .background(Color.Black.copy(alpha = 0.55f), RoundedCornerShape(6.dp))
                        .clickable { onRemove(image.id) }
                        .padding(3.dp)
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
            modifier = Modifier.size(48.dp),
            shape = RoundedCornerShape(8.dp),
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
private fun EmptyConversation(onPromptSelected: (String) -> Unit) {
    Column(
        modifier = Modifier.fillMaxWidth().padding(top = 48.dp, start = 8.dp, end = 8.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text("新对话", fontSize = 20.sp, color = MaterialTheme.colorScheme.onBackground, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(6.dp))
        Text(
            "有什么我可以帮你完成的？",
            color = mutedInk(),
            fontSize = 15.sp
        )
        Spacer(Modifier.height(22.dp))
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            PromptSuggestion(Icons.Filled.Lightbulb, "帮我理清思路", "把一个复杂问题拆解成清晰的行动步骤", onPromptSelected)
            PromptSuggestion(Icons.Filled.EditNote, "帮我写点东西", "帮我起草一份简洁、专业的内容", onPromptSelected)
            PromptSuggestion(Icons.Filled.Code, "分析代码或文件", "请分析我接下来发送的代码或文件", onPromptSelected)
        }
    }
}

@Composable
private fun PromptSuggestion(
    icon: ImageVector,
    title: String,
    prompt: String,
    onSelected: (String) -> Unit
) {
    Surface(
        onClick = { onSelected(prompt) },
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.surface,
        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant)
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(icon, contentDescription = null, tint = mutedInk(), modifier = Modifier.size(19.dp))
            Spacer(Modifier.width(12.dp))
            Column {
                Text(title, fontWeight = FontWeight.Medium, color = MaterialTheme.colorScheme.onSurface)
                Text(prompt, fontSize = 11.sp, color = mutedInk(), maxLines = 1)
            }
        }
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
    val downloads = remember(user, message.content) {
        if (user) emptyList() else extractDownloadableLinks(message.content)
    }
    val visibleText = remember(message.content, downloads) {
        downloads.fold(message.content) { text, url -> text.replace(url, "") }
            .replace(Regex("\\n{3,}"), "\n\n")
            .trim()
    }

    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (user) Arrangement.End else Arrangement.Start) {
        Surface(
            modifier = if (user) Modifier.fillMaxWidth(0.82f) else Modifier.weight(1f),
            color = if (user) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface,
            shape = RoundedCornerShape(8.dp),
            border = if (user) null else androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
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
                                modifier = Modifier.size(150.dp).background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(8.dp))
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
                                .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(8.dp))
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
                                .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(8.dp))
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
    val blocks = remember(text) { parseMarkdownBlocks(text) }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        blocks.forEach { block ->
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
                    Modifier.fillMaxWidth().background(Color(0xFF1D282A), RoundedCornerShape(8.dp)).padding(10.dp)
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
            .fillMaxWidth(0.70f)
            .widthIn(max = 280.dp)
            .background(MaterialTheme.colorScheme.surface)
            .statusBarsPadding()
            .clickable(enabled = false) {}
            .padding(horizontal = 12.dp, vertical = 8.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("对话", fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurface, modifier = Modifier.weight(1f))
            IconButton(onClick = onRefresh, modifier = Modifier.size(38.dp)) {
                Icon(Icons.Filled.Refresh, contentDescription = "刷新对话", modifier = Modifier.size(19.dp), tint = mutedInk())
            }
            IconButton(onClick = onClose, modifier = Modifier.size(38.dp)) {
                Icon(Icons.Filled.Close, contentDescription = "关闭对话列表", modifier = Modifier.size(19.dp), tint = mutedInk())
            }
        }
        Spacer(Modifier.height(10.dp))
        Button(
            onClick = onNew,
            modifier = Modifier.fillMaxWidth().height(42.dp),
            shape = RoundedCornerShape(8.dp)
        ) {
            Icon(Icons.Filled.Add, contentDescription = null, modifier = Modifier.size(19.dp))
            Spacer(Modifier.width(8.dp))
            Text("新建对话", fontSize = 14.sp)
        }
        Spacer(Modifier.height(8.dp))
        Surface(
            modifier = Modifier.fillMaxWidth().height(40.dp),
            shape = RoundedCornerShape(6.dp),
            color = MaterialTheme.colorScheme.background,
            border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        ) {
            Row(
                modifier = Modifier.fillMaxSize().padding(horizontal = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    Icons.Filled.Search,
                    contentDescription = null,
                    modifier = Modifier.size(18.dp),
                    tint = mutedInk(),
                )
                Spacer(Modifier.width(8.dp))
                BasicTextField(
                    value = search,
                    onValueChange = { search = it },
                    modifier = Modifier.weight(1f),
                    singleLine = true,
                    textStyle = MaterialTheme.typography.bodyMedium.copy(color = MaterialTheme.colorScheme.onSurface),
                    cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                    decorationBox = { innerTextField ->
                        Box {
                            if (search.isEmpty()) {
                                Text("搜索对话", color = mutedInk(), style = MaterialTheme.typography.bodyMedium)
                            }
                            innerTextField()
                        }
                    },
                )
            }
        }
        Spacer(Modifier.height(10.dp))
        Text("最近对话", color = mutedInk(), fontSize = 12.sp, modifier = Modifier.padding(horizontal = 4.dp))
        Spacer(Modifier.height(5.dp))

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
                        Icon(
                            if (expanded) Icons.Filled.KeyboardArrowDown else Icons.Filled.ChevronRight,
                            contentDescription = null,
                            tint = mutedInk(),
                            modifier = Modifier.size(18.dp),
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(group.channel.label, color = mutedInk(), fontSize = 13.sp, fontWeight = FontWeight.Medium, modifier = Modifier.weight(1f))
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
                                        RoundedCornerShape(6.dp)
                                    )
                                    .clickable {
                                        menuSessionId = null
                                        onSelect(session.id)
                                    }
                                    .padding(start = 12.dp, end = 2.dp, top = 9.dp, bottom = 9.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Column(Modifier.weight(1f)) {
                                    Text(
                                        session.displayTitle,
                                        color = MaterialTheme.colorScheme.onSurface,
                                        fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal,
                                        maxLines = 1,
                                        fontSize = 14.sp
                                    )
                                    Text("${session.messageCount} 条消息", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 11.sp)
                                }
                                Box {
                                    IconButton(
                                        onClick = { menuSessionId = session.id },
                                        modifier = Modifier.size(38.dp)
                                    ) {
                                        Icon(
                                            Icons.Filled.MoreVert,
                                            contentDescription = "对话操作",
                                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                            modifier = Modifier.size(19.dp)
                                        )
                                    }
                                    DropdownMenu(
                                        expanded = menuSessionId == session.id,
                                        onDismissRequest = { menuSessionId = null }
                                    ) {
                                        DropdownMenuItem(
                                            text = { Text("重命名") },
                                            onClick = {
                                                menuSessionId = null
                                                onRename(session)
                                            }
                                        )
                                        DropdownMenuItem(
                                            text = { Text("删除", color = MaterialTheme.colorScheme.error) },
                                            onClick = {
                                                menuSessionId = null
                                                onDelete(session)
                                            }
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
