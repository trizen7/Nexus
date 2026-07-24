package app.nexus.mobile

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import app.nexus.mobile.network.ChatFile
import app.nexus.mobile.network.ChatImage
import app.nexus.mobile.network.ChatMessage
import app.nexus.mobile.network.ChatRole
import app.nexus.mobile.network.HermesApiClient
import app.nexus.mobile.network.HermesCronJob
import app.nexus.mobile.network.HermesModel
import app.nexus.mobile.network.HermesSession
import app.nexus.mobile.network.HermesStreamEvent
import app.nexus.mobile.network.friendlyNetworkError
import app.nexus.mobile.network.requiresPasswordReauthentication
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.UUID

data class MainUiState(
    val serverUrl: String = "",
    val username: String = "",
    val password: String = "",
    val token: String = "",
    val connectionStatus: ConnectionStatus = ConnectionStatus.NOT_CONFIGURED,
    val gatewayVersion: String? = null,
    val hermesVersion: String? = null,
    val sessions: List<HermesSession> = emptyList(),
    val activeSessionId: String? = null,
    val personaModels: List<HermesModel> = emptyList(),
    val selectedPersonaModelId: String? = null,
    val inferenceModels: List<HermesModel> = emptyList(),
    val selectedInferenceModelId: String? = null,
    val selectedReasoningEffort: ReasoningEffort = ReasoningEffort.DEFAULT,
    val modelsLoading: Boolean = false,
    val modelPicker: ModelPickerKind? = null,
    val cronManagerOpen: Boolean = false,
    val cronJobs: List<HermesCronJob> = emptyList(),
    val cronJobsLoading: Boolean = false,
    val cronBusyJobId: String? = null,
    val cronEditor: CronJobEditorState? = null,
    val cronJobToDelete: HermesCronJob? = null,
    val cronNotice: String? = null,
    val messages: List<ChatMessage> = emptyList(),
    val pendingImages: List<ChatImage> = emptyList(),
    val pendingFiles: List<ChatFile> = emptyList(),
    val uploadProgress: Int? = null,
    val preparingImage: Boolean = false,
    val drawerOpen: Boolean = false,
    val featurePanelOpen: Boolean = false,
    val voiceInputMode: Boolean = false,
    val voiceListening: Boolean = false,
    val voicePreview: String = "",
    val settingsOpen: Boolean = false,
    val autoRefresh: Boolean = true,
    val themeMode: ThemeMode = ThemeMode.SYSTEM,
    val loading: Boolean = false,
    val loadingOlder: Boolean = false,
    val hasMoreMessages: Boolean = false,
    val loadedMessageCount: Int = 0,
    val historyPrependCount: Int = 0,
    val historyPrependToken: Long = 0L,
    val initialScrollToken: Long = 0L,
    val streaming: Boolean = false,
    val runStoppable: Boolean = false,
    val thinking: Boolean = false,
    val toolStatus: String? = null,
    val answerStatus: AnswerStatus = AnswerStatus.IDLE,
    val sessionToRename: HermesSession? = null,
    val sessionToDelete: HermesSession? = null,
    val selectedDownload: ChatFile? = null,
    val downloadStates: Map<String, FileDownloadState> = emptyMap(),
    val error: String? = null
) {
    val activeSession: HermesSession?
        get() = sessions.firstOrNull { it.id == activeSessionId }

    val selectedPersonaModel: HermesModel?
        get() = personaModels.firstOrNull { it.id == selectedPersonaModelId }

    val selectedInferenceModel: HermesModel?
        get() = inferenceModels.firstOrNull { it.id == selectedInferenceModelId }

    val selectedPersonaModelLabel: String
        get() = selectedPersonaModel?.displayName ?: selectedPersonaModelId ?: "Hermes 默认（default）"

    val selectedInferenceModelLabel: String
        get() = selectedInferenceModel?.displayName ?: selectedInferenceModelId ?: "Hermes 默认"

    val selectedRuntimeSummary: String
        get() = "$selectedInferenceModelLabel · 推理 ${selectedReasoningEffort.label}"

}

enum class ConnectionStatus { NOT_CONFIGURED, CONNECTING, CONNECTED, FAILED }

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private companion object {
        const val MESSAGE_PAGE_SIZE = 10
        const val OLDER_MESSAGE_PAGE_SIZE = 20
        const val DRAFT_PERSIST_DEBOUNCE_MILLIS = 350L
    }
    private val connectionStore = ConnectionStore(application)
    private val savedConnection = connectionStore.load()
    private val persistedDraftBundle = connectionStore.loadDrafts()
    private val persistedRuntimeConfigBundle = connectionStore.loadRuntimeConfigs()
    private var localDraftKey = persistedDraftBundle.localDraftKey
        .ifBlank { persistedRuntimeConfigBundle.localDraftKey }
        .ifBlank { "local-draft-${UUID.randomUUID()}" }
    private val migratedRuntimeConfigBundle = migrateRuntimeConfigBundle(
        bundle = persistedRuntimeConfigBundle,
        activeSessionId = savedConnection.activeSessionId,
        localDraftKey = localDraftKey,
        legacyInferenceModelId = savedConnection.selectedInferenceModelId,
        legacyReasoningEffort = savedConnection.selectedReasoningEffort
    )
    private var runtimeConfigs = migratedRuntimeConfigBundle.configs
        .mapValues { it.value.toRuntimeConfig() }
        .toMutableMap()
    private val initialRuntimeConfig = runtimeConfigs[savedConnection.activeSessionId ?: localDraftKey]
        ?: ConversationRuntimeConfig()
    private val _input = MutableStateFlow("")
    val input: StateFlow<String> = _input.asStateFlow()

    private val _uiState = MutableStateFlow(
        MainUiState(
            serverUrl = savedConnection.serverUrl.ifBlank(::defaultServerUrl),
            username = savedConnection.username,
            password = "",
            token = savedConnection.token,
            activeSessionId = savedConnection.activeSessionId,
            selectedPersonaModelId = savedConnection.selectedPersonaModelId,
            selectedInferenceModelId = initialRuntimeConfig.inferenceModelId,
            selectedReasoningEffort = initialRuntimeConfig.reasoningEffort,
            autoRefresh = savedConnection.autoRefresh,
            themeMode = savedConnection.themeMode,
            connectionStatus = if (savedConnection.isUsable) ConnectionStatus.CONNECTING else ConnectionStatus.NOT_CONFIGURED
        )
    )
    val uiState: StateFlow<MainUiState> = _uiState.asStateFlow()

    private var client: HermesApiClient? = null
    private var streamJob: Job? = null
    private var answerStatusJob: Job? = null
    private val fileUploadJobs = mutableMapOf<String, Job>()
    private val imageUploadJobs = mutableMapOf<String, Job>()
    private val downloadJobs = mutableMapOf<String, Job>()
    private val downloadGenerations = mutableMapOf<String, Long>()
    private var pollingJob: Job? = null
    private var sessionLoadJob: Job? = null
    private var runStatusJob: Job? = null
    private var draftPersistJob: Job? = null
    private var liveAssistantMessageId: String? = null
    private var stopRequestedSessionId: String? = null
    private var sessionLoadGeneration = 0L
    private val observedActiveRuns = mutableSetOf<String>()
    private var drafts = ConversationDrafts()
    private val draftImages = mutableMapOf<String, List<ChatImage>>()
    private val draftFiles = mutableMapOf<String, List<ChatFile>>()

    init {
        persistRuntimeConfigs()
        connectionStore.clearLegacyRuntimeSelections()
        restorePersistedDraftBundle()
        if (savedConnection.isUsable) connect()
    }

    fun updateConnection(serverUrl: String, username: String, password: String) {
        _uiState.update {
            it.copy(serverUrl = serverUrl, username = username, password = password, token = "", error = null)
        }
    }

    fun connect() {
        val normalizedUrl = normalizeServerUrl(_uiState.value.serverUrl)
        serverUrlValidationError(normalizedUrl)?.let { message ->
            _uiState.update { it.copy(serverUrl = normalizedUrl, error = message) }
            return
        }
        _uiState.update { it.copy(serverUrl = normalizedUrl, error = null) }
        connectConfirmed()
    }

    private fun connectConfirmed() {
        val snapshot = _uiState.value
        val normalizedUrl = normalizeServerUrl(snapshot.serverUrl)
        if (normalizedUrl.isBlank() || snapshot.username.isBlank() || (snapshot.token.isBlank() && snapshot.password.isBlank())) {
            _uiState.update { it.copy(serverUrl = normalizedUrl, error = "请填写服务器、账号和密码") }
            return
        }
        serverUrlValidationError(normalizedUrl)?.let { message ->
            _uiState.update { it.copy(serverUrl = normalizedUrl, error = message) }
            return
        }
        viewModelScope.launch {
            _uiState.update {
                it.copy(
                    serverUrl = normalizedUrl,
                    connectionStatus = ConnectionStatus.CONNECTING,
                    loading = true,
                    error = null
                )
            }
            runCatching {
                val api = HermesApiClient(normalizedUrl, snapshot.token)
                val accessToken = snapshot.token.ifBlank { api.login(snapshot.username, snapshot.password) }
                val health = api.health()
                val sessions = visibleSessions(api.listSessions())
                client = api
                Triple(accessToken, health, sessions)
            }.onSuccess { (accessToken, health, sessions) ->
                val selected = resolveVisibleActiveSessionId(sessions, _uiState.value.activeSessionId)
                connectionStore.saveLogin(normalizedUrl, snapshot.username, accessToken, selected)
                _uiState.update { state ->
                    applyRuntimeConfig(
                        state.copy(
                            password = "",
                            token = accessToken,
                            connectionStatus = ConnectionStatus.CONNECTED,
                            gatewayVersion = health.gatewayVersion,
                            hermesVersion = health.hermesVersion,
                            sessions = sessions,
                            activeSessionId = selected,
                            loading = false,
                            error = null
                        ),
                        selected
                    )
                }
                refreshModels(showErrors = false)
                if (selected != null) {
                    loadSession(selected)
                    refreshActiveRunStatus()
                } else createSession()
            }.onFailure { error ->
                val tokenExpired = snapshot.token.isNotBlank() && requiresPasswordReauthentication(error)
                if (tokenExpired) {
                    connectionStore.clearToken()
                    client = null
                }
                _uiState.update {
                    it.copy(
                        token = if (tokenExpired) "" else it.token,
                        connectionStatus = ConnectionStatus.FAILED,
                        loading = false,
                        error = friendlyNetworkError(error, normalizedUrl)
                    )
                }
            }
        }
    }

    fun refreshSessions() {
        val api = client ?: return
        viewModelScope.launch {
            runCatching { visibleSessions(api.listSessions()) }
                .onSuccess { sessions ->
                    val currentSessionId = _uiState.value.activeSessionId
                    val selected = resolveVisibleActiveSessionId(
                        sessions = sessions,
                        preferredSessionId = currentSessionId,
                        chooseFirstWhenMissing = currentSessionId != null
                    )
                    _uiState.update { state ->
                        val updated = state.copy(
                            sessions = sessions,
                            activeSessionId = selected,
                            messages = if (currentSessionId != selected && selected == null) emptyList() else state.messages
                        )
                        if (currentSessionId != selected) applyRuntimeConfig(updated, selected) else updated
                    }
                    if (currentSessionId != selected) {
                        connectionStore.saveActiveSession(selected)
                        if (selected != null) {
                            restoreDraft(selected)
                            loadSession(selected)
                        } else {
                            createSession()
                        }
                    }
                }
                .onFailure { showError(it) }
        }
    }

    fun onAppResume() {
        when (_uiState.value.connectionStatus) {
            ConnectionStatus.CONNECTED -> {
                refreshSessions()
                refreshCurrentMessages()
                refreshActiveRunStatus()
                startPolling()
            }
            ConnectionStatus.FAILED, ConnectionStatus.CONNECTING -> if (_uiState.value.token.isNotBlank()) connect()
            ConnectionStatus.NOT_CONFIGURED -> Unit
        }
    }

    fun refreshFromForeground() {
        refreshSessions()
        refreshCurrentMessages()
        refreshActiveRunStatus()
    }

    internal fun refreshActiveRunStatus() {
        val api = client ?: return
        val sessionId = _uiState.value.activeSessionId ?: return
        runStatusJob?.cancel()
        runStatusJob = viewModelScope.launch {
            runCatching { api.getSessionRunStatus(sessionId) }
                .onSuccess { status ->
                    if (_uiState.value.activeSessionId != sessionId) return@onSuccess
                    if (stopRequestedSessionId == sessionId) {
                        if (status.active || status.status in setOf("queued", "running", "stopping")) return@onSuccess
                        stopRequestedSessionId = null
                        observedActiveRuns.remove(sessionId)
                        _uiState.update { it.copy(streaming = false, runStoppable = false, thinking = false, toolStatus = null) }
                        showTransientAnswerStatus(AnswerStatus.STOPPED)
                        refreshCurrentMessages()
                        return@onSuccess
                    }
                    when {
                        status.active || status.status in setOf("queued", "running", "stopping") -> {
                            observedActiveRuns += sessionId
                            _uiState.update { state ->
                                val answerStatus = when (status.phase) {
                                    "generating" -> AnswerStatus.GENERATING
                                    "tool" -> AnswerStatus.TOOL
                                    else -> AnswerStatus.THINKING
                                }
                                if (stopRequestedSessionId == sessionId) return@update state
                                val messages = reconcileRunSnapshot(
                                    state.messages,
                                    liveAssistantMessageId,
                                    status.runId,
                                    status.snapshot
                                )
                                state.copy(
                                    streaming = true,
                                    runStoppable = status.stoppable,
                                    thinking = status.phase !in setOf("generating", "tool"),
                                    toolStatus = status.toolName?.let { "正在使用 $it…" },
                                    answerStatus = answerStatus,
                                    messages = messages
                                )
                            }
                        }
                        status.status == "completed" && observedActiveRuns.remove(sessionId) -> {
                            if (stopRequestedSessionId == sessionId) {
                                showTransientAnswerStatus(AnswerStatus.STOPPED)
                                return@onSuccess
                            }
                            _uiState.update {
                                it.copy(
                                    streaming = false,
                                    runStoppable = false,
                                    thinking = false,
                                    toolStatus = null,
                                    messages = it.messages.filterNot { message -> message.id.startsWith("run-snapshot-") }
                                )
                            }
                            showTransientAnswerStatus(AnswerStatus.COMPLETED)
                            refreshCurrentMessages()
                        }
                        status.status == "failed" && observedActiveRuns.remove(sessionId) -> {
                            _uiState.update { it.copy(streaming = false, runStoppable = false, thinking = false, toolStatus = null) }
                            showTransientAnswerStatus(AnswerStatus.FAILED)
                            refreshCurrentMessages()
                        }
                        status.status == "stopped" && observedActiveRuns.remove(sessionId) -> {
                            stopRequestedSessionId = null
                            _uiState.update { it.copy(streaming = false, runStoppable = false, thinking = false, toolStatus = null) }
                            showTransientAnswerStatus(AnswerStatus.STOPPED)
                            refreshCurrentMessages()
                        }
                        status.status == "interrupted" -> {
                            observedActiveRuns.remove(sessionId)
                            _uiState.update { state ->
                                val withoutSnapshot = state.messages.filterNot { it.id.startsWith("run-snapshot-") }
                                val messages = if (status.snapshot.isNotBlank()) {
                                    withoutSnapshot + ChatMessage(
                                        "run-snapshot-${status.runId ?: sessionId}",
                                        ChatRole.ASSISTANT,
                                        status.snapshot
                                    )
                                } else withoutSnapshot
                                state.copy(
                                    streaming = false,
                                    runStoppable = false,
                                    thinking = false,
                                    toolStatus = null,
                                    answerStatus = AnswerStatus.FAILED,
                                    messages = messages,
                                    error = status.message ?: "回答连接已中断"
                                )
                            }
                        }
                        else -> {
                            val wasObserved = observedActiveRuns.remove(sessionId)
                            _uiState.update {
                                it.copy(
                                    streaming = false,
                                    runStoppable = false,
                                    thinking = false,
                                    toolStatus = null,
                                    answerStatus = AnswerStatus.IDLE,
                                    messages = it.messages.filterNot { message -> message.id.startsWith("run-snapshot-") }
                                )
                            }
                            if (wasObserved) refreshCurrentMessages()
                        }
                    }
                }
                .onFailure {
                    // Older servers do not expose run status. Unknown means connected,
                    // never "completed" or "still processing" by guesswork.
                    if (_uiState.value.activeSessionId == sessionId) {
                        _uiState.update { it.copy(answerStatus = AnswerStatus.IDLE) }
                    }
                }
        }
    }

    private fun refreshCurrentMessages() {
        val api = client ?: return
        val sessionId = _uiState.value.activeSessionId ?: return
        if (_uiState.value.streaming || _uiState.value.loading || _uiState.value.loadingOlder) return
        val limit = _uiState.value.loadedMessageCount.coerceAtLeast(MESSAGE_PAGE_SIZE)
        viewModelScope.launch {
            runCatching { api.loadMessagePage(sessionId, limit, 0) }
                .onSuccess { page ->
                    _uiState.update { state ->
                        if (state.activeSessionId != sessionId || state.streaming) state
                        else {
                            val merged = mergeAuthoritativeMessages(state.messages, page.messages)
                            connectionStore.saveMessageCache(sessionId, merged)
                            state.copy(
                                messages = merged,
                                loadedMessageCount = merged.size,
                                hasMoreMessages = page.hasMore
                            )
                        }
                    }
                }
        }
    }

    fun startPolling() {
        if (!_uiState.value.autoRefresh || pollingJob?.isActive == true) return
        pollingJob = viewModelScope.launch {
            while (isActive) {
                delay(6_000)
                if (!_uiState.value.loadingOlder) refreshCurrentMessages()
                refreshActiveRunStatus()
            }
        }
    }

    fun stopPolling() {
        pollingJob?.cancel()
        pollingJob = null
    }

    fun createSession() {
        saveCurrentDraft()
        streamJob?.cancel()
        liveAssistantMessageId = null
        connectionStore.saveActiveSession(null)
        localDraftKey = "local-draft-${UUID.randomUUID()}"
        persistRuntimeConfigs()
        _uiState.update { state ->
            applyRuntimeConfig(
                state.copy(
                    activeSessionId = null,
                    messages = emptyList(),
                    pendingImages = emptyList(),
                    pendingFiles = emptyList(),
                    drawerOpen = false,
                    featurePanelOpen = false,
                    voiceInputMode = false,
                    loading = false,
                    streaming = false,
                    thinking = false,
                    toolStatus = null,
                    error = null
                ),
                null
            )
        }
        restoreDraft(null)
    }

    private suspend fun persistDraftSession(): String {
        val api = client ?: error("尚未连接 Hermes")
        val draftRuntimeKey = localDraftKey
        val session = api.createSession()
        moveRuntimeConfig(draftRuntimeKey, session.id)
        connectionStore.saveActiveSession(session.id)
        _uiState.update {
            it.copy(
                sessions = listOf(session) + it.sessions.filterNot { old -> old.id == session.id },
                activeSessionId = session.id
            )
        }
        return session.id
    }

    fun selectSession(sessionId: String) {
        saveCurrentDraft()
        liveAssistantMessageId = null
        connectionStore.saveActiveSession(sessionId)
        _uiState.update { state ->
            applyRuntimeConfig(
                state.copy(
                    drawerOpen = false,
                    featurePanelOpen = false,
                    pendingImages = emptyList(),
                    pendingFiles = emptyList(),
                    preparingImage = false,
                    streaming = false,
                    thinking = false,
                    toolStatus = null,
                    answerStatus = AnswerStatus.IDLE
                ),
                sessionId
            )
        }
        restoreDraft(sessionId)
        loadSession(sessionId)
        refreshActiveRunStatus()
    }

    fun requestRename(session: HermesSession) {
        _uiState.update { it.copy(sessionToRename = session) }
    }

    fun cancelRename() {
        _uiState.update { it.copy(sessionToRename = null) }
    }

    fun renameSession(title: String) {
        val api = client ?: return
        val session = _uiState.value.sessionToRename ?: return
        val normalized = title.trim()
        if (normalized.isEmpty()) {
            _uiState.update { it.copy(error = "对话标题不能为空") }
            return
        }
        viewModelScope.launch {
            runCatching { api.renameSession(session.id, normalized) }
                .onSuccess { renamed ->
                    _uiState.update { state ->
                        state.copy(
                            sessions = state.sessions.map { if (it.id == renamed.id) renamed else it },
                            sessionToRename = null
                        )
                    }
                }
                .onFailure { showError(it) }
        }
    }

    fun requestDelete(session: HermesSession) {
        _uiState.update { it.copy(sessionToDelete = session) }
    }

    fun cancelDelete() {
        _uiState.update { it.copy(sessionToDelete = null) }
    }

    fun confirmDelete() {
        val api = client ?: return
        val session = _uiState.value.sessionToDelete ?: return
        viewModelScope.launch {
            runCatching { api.deleteSession(session.id) }
                .onSuccess { deleted ->
                    if (!deleted) {
                        showError(IllegalStateException("服务器未删除该对话"))
                        return@onSuccess
                    }
                    val state = _uiState.value
                    val wasActive = state.activeSessionId == session.id
                    val remaining = state.sessions.filterNot { it.id == session.id }
                    val next = if (wasActive) remaining.firstOrNull()?.id else state.activeSessionId
                    connectionStore.clearMessageCache(session.id)
                    removeRuntimeConfig(session.id)
                    drafts = drafts.remove(session.id)
                    draftImages.remove(session.id)
                    draftFiles.remove(session.id)
                    persistAllDrafts()
                    connectionStore.saveActiveSession(next)
                    _uiState.update { current ->
                        val updated = current.copy(
                            sessions = remaining,
                            activeSessionId = next,
                            messages = if (wasActive) emptyList() else current.messages,
                            pendingImages = if (wasActive) emptyList() else current.pendingImages,
                            pendingFiles = if (wasActive) emptyList() else current.pendingFiles,
                            sessionToDelete = null
                        )
                        if (wasActive) applyRuntimeConfig(updated, next) else updated
                    }
                    if (wasActive) {
                        if (next != null) {
                            restoreDraft(next)
                            loadSession(next)
                        } else {
                            _input.value = ""
                            createSession()
                        }
                    }
                }
                .onFailure { showError(it) }
        }
    }

    fun loadSession(sessionId: String) {
        val api = client ?: return
        streamJob?.cancel()
        sessionLoadJob?.cancel()
        val generation = ++sessionLoadGeneration
        val cached = connectionStore.loadMessageCache(sessionId)
        _uiState.update { state ->
            applyRuntimeConfig(
                state.copy(
                    activeSessionId = sessionId,
                    messages = cached,
                    loading = cached.isEmpty(),
                    loadingOlder = false,
                    loadedMessageCount = cached.size,
                    hasMoreMessages = true,
                    historyPrependCount = 0,
                    initialScrollToken = state.initialScrollToken + 1,
                    error = null
                ),
                sessionId
            )
        }
        refreshActiveRunStatus()
        sessionLoadJob = viewModelScope.launch {
            runCatching { api.loadMessagePage(sessionId, MESSAGE_PAGE_SIZE, 0) }
                .onSuccess { page ->
                    _uiState.update { state ->
                        if (!acceptsSessionLoad(sessionId, state.activeSessionId, generation, sessionLoadGeneration)) state
                        else {
                            connectionStore.saveMessageCache(sessionId, page.messages)
                            state.copy(
                                messages = page.messages,
                                loading = false,
                                loadedMessageCount = page.messages.size,
                                hasMoreMessages = page.hasMore,
                                initialScrollToken = state.initialScrollToken + 1
                            )
                        }
                    }
                }
                .onFailure { error ->
                    if (error is kotlinx.coroutines.CancellationException) return@onFailure
                    val accepted = acceptsSessionLoad(sessionId, _uiState.value.activeSessionId, generation, sessionLoadGeneration)
                    if (accepted) {
                        _uiState.update { it.copy(loading = false) }
                        showError(error)
                    }
                }
        }
    }

    fun loadOlderMessages() {
        val api = client ?: return
        val state = _uiState.value
        val sessionId = state.activeSessionId ?: return
        if (state.loading || state.loadingOlder || !state.hasMoreMessages) return
        val generation = sessionLoadGeneration
        _uiState.update { it.copy(loadingOlder = true) }
        viewModelScope.launch {
            runCatching { api.loadMessagePage(sessionId, OLDER_MESSAGE_PAGE_SIZE, state.loadedMessageCount) }
                .onSuccess { page ->
                    _uiState.update { current ->
                        if (!acceptsSessionLoad(sessionId, current.activeSessionId, generation, sessionLoadGeneration)) current
                        else {
                            val merged = prependMessagePage(current.messages, page.messages)
                            connectionStore.saveMessageCache(sessionId, merged)
                            current.copy(
                                messages = merged,
                                loadingOlder = false,
                                loadedMessageCount = nextLoadedMessageCount(state.loadedMessageCount, page.messages.size),
                                hasMoreMessages = page.hasMore,
                                historyPrependCount = page.messages.size,
                                historyPrependToken = current.historyPrependToken + 1
                            )
                        }
                    }
                }
                .onFailure { error ->
                    if (error is kotlinx.coroutines.CancellationException) return@onFailure
                    if (acceptsSessionLoad(sessionId, _uiState.value.activeSessionId, generation, sessionLoadGeneration)) {
                        _uiState.update { it.copy(loadingOlder = false) }
                        showError(error)
                    }
                }
        }
    }

    fun updateInput(value: String) {
        _input.value = value
        saveCurrentDraft(persistImmediately = false)
    }

    private fun runtimeKey(sessionId: String? = _uiState.value.activeSessionId): String = sessionId ?: localDraftKey

    private fun runtimeConfig(sessionId: String? = _uiState.value.activeSessionId): ConversationRuntimeConfig =
        runtimeConfigs[runtimeKey(sessionId)] ?: ConversationRuntimeConfig()

    private fun applyRuntimeConfig(state: MainUiState, sessionId: String?): MainUiState {
        val config = runtimeConfig(sessionId)
        return state.copy(
            selectedInferenceModelId = config.inferenceModelId,
            selectedReasoningEffort = config.reasoningEffort
        )
    }

    private fun saveRuntimeConfig(key: String, config: ConversationRuntimeConfig) {
        if (config.isDefault) runtimeConfigs.remove(key) else runtimeConfigs[key] = config
        persistRuntimeConfigs()
    }

    private fun moveRuntimeConfig(fromKey: String, toKey: String) {
        val config = runtimeConfigs.remove(fromKey)
        if (config != null && !config.isDefault) runtimeConfigs[toKey] = config
        persistRuntimeConfigs()
    }

    private fun removeRuntimeConfig(key: String) {
        runtimeConfigs.remove(key)
        persistRuntimeConfigs()
    }

    private fun persistRuntimeConfigs() {
        val stored = runtimeConfigs
            .filterValues { !it.isDefault }
            .mapValues { it.value.toPersistedRuntimeConfig() }
        connectionStore.saveRuntimeConfigs(
            PersistedRuntimeConfigBundle(
                schemaVersion = RUNTIME_CONFIG_SCHEMA_VERSION,
                localDraftKey = localDraftKey,
                configs = stored
            )
        )
    }

    private fun draftKey(sessionId: String? = _uiState.value.activeSessionId): String = sessionId ?: localDraftKey

    private fun saveCurrentDraft(persistImmediately: Boolean = true) {
        val state = _uiState.value
        val key = draftKey()
        drafts = drafts.save(
            key,
            ComposerDraft(
                text = _input.value,
                imageIds = state.pendingImages.map(ChatImage::id),
                fileIds = state.pendingFiles.map(ChatFile::id)
            )
        )
        draftImages[key] = state.pendingImages
        draftFiles[key] = state.pendingFiles
        if (persistImmediately) flushDrafts() else scheduleDraftPersistence()
    }

    private fun scheduleDraftPersistence() {
        draftPersistJob?.cancel()
        draftPersistJob = viewModelScope.launch {
            delay(DRAFT_PERSIST_DEBOUNCE_MILLIS)
            persistAllDrafts()
            draftPersistJob = null
        }
    }

    fun flushDrafts() {
        draftPersistJob?.cancel()
        draftPersistJob = null
        persistAllDrafts()
    }

    private fun restoreDraft(sessionId: String?) {
        val key = draftKey(sessionId)
        val draft = drafts.load(key)
        _input.value = draft.text
        val images = draftImages[key].orEmpty()
        val files = draftFiles[key].orEmpty()
        _uiState.update {
            it.copy(
                pendingImages = images,
                pendingFiles = files,
                preparingImage = false
            )
        }
    }

    private fun restorePersistedDraftBundle() {
        persistedDraftBundle.drafts.forEach { (key, stored) ->
            val images = stored.images.map(PersistedDraftImage::toImage)
            val files = stored.resolvedFiles().map(PersistedDraftFile::toFile)
            drafts = drafts.save(
                key,
                ComposerDraft(
                    text = stored.text,
                    imageIds = images.map(ChatImage::id),
                    fileIds = files.map(ChatFile::id)
                )
            )
            draftImages[key] = images
            draftFiles[key] = files
        }
        if (savedConnection.activeSessionId != null) {
            restoreDraft(savedConnection.activeSessionId)
        } else if (persistedDraftBundle.localDraftKey.isNotBlank()) {
            restoreDraft(null)
        }
    }

    private fun persistAllDrafts() {
        val keys = (drafts.keys() + draftImages.keys + draftFiles.keys + draftKey()).toSet()
        val stored = keys.associateWith { key ->
            val draft = drafts.load(key)
            PersistedComposerDraft(
                text = draft.text,
                images = draftImages[key].orEmpty().map {
                    PersistedDraftImage(it.id, it.previewUri, it.uploadedId)
                },
                files = draftFiles[key].orEmpty().map {
                    PersistedDraftFile(it.id, it.name, it.mimeType, it.size, it.uri, it.uploadedId, it.downloadUrl)
                }
            )
        }.filterValues { it.text.isNotBlank() || it.images.isNotEmpty() || it.files.isNotEmpty() }
        connectionStore.saveDrafts(PersistedDraftBundle(localDraftKey, stored))
    }

    fun prepareImage(uri: Uri) {
        viewModelScope.launch {
            setPreparingImage(true)
            runCatching { ImageProcessor.prepare(getApplication(), uri) }
                .onSuccess(::addImage)
                .onFailure { showImageError(it.message ?: "图片处理失败") }
        }
    }

    fun addImage(image: ChatImage) {
        val queued = image.copy(uploadState = AttachmentUploadState.Uploading(0))
        var accepted = false
        _uiState.update { state ->
            if (state.pendingImages.size >= 4) state.copy(error = "一次最多发送 4 张图片", preparingImage = false)
            else {
                accepted = true
                state.copy(pendingImages = state.pendingImages + queued, preparingImage = false, featurePanelOpen = false)
            }
        }
        if (accepted) uploadImage(queued)
    }

    private fun uploadImage(image: ChatImage) {
        val api = client ?: return
        val app = getApplication<Application>()
        NotificationHelper.showTransfer(app, image.id, "图片.jpg", 0, uploading = true)
        imageUploadJobs[image.id]?.cancel()
        imageUploadJobs[image.id] = viewModelScope.launch {
            runCatching {
                val bytes = image.uploadBytes ?: error("图片数据无效")
                api.uploadFile("图片.jpg", "image/jpeg", bytes)
            }.onSuccess { uploaded ->
                NotificationHelper.finishTransfer(app, image.id, "图片.jpg", uploading = true, success = true)
                val remotePreview = resolveDownloadUrl(_uiState.value.serverUrl, uploaded.downloadUrl)
                _uiState.update { state ->
                    state.copy(pendingImages = state.pendingImages.map {
                        if (it.id == image.id) it.copy(
                            previewUri = remotePreview,
                            dataUrl = remotePreview,
                            uploadBytes = null,
                            uploadedId = uploaded.id,
                            uploadState = AttachmentUploadState.Ready(uploaded.id)
                        ) else it
                    })
                }
                saveCurrentDraft()
            }.onFailure { error ->
                if (error is kotlinx.coroutines.CancellationException) return@onFailure
                NotificationHelper.finishTransfer(app, image.id, "图片.jpg", uploading = true, success = false)
                _uiState.update { state ->
                    state.copy(pendingImages = state.pendingImages.map {
                        if (it.id == image.id) it.copy(uploadState = AttachmentUploadState.Failed(friendlyNetworkError(error))) else it
                    })
                }
            }
        }
    }

    fun retryImageUpload(imageId: String) {
        val image = _uiState.value.pendingImages.firstOrNull { it.id == imageId } ?: return
        _uiState.update { state ->
            state.copy(pendingImages = state.pendingImages.map {
                if (it.id == imageId) it.copy(uploadState = AttachmentUploadState.Uploading(0)) else it
            })
        }
        uploadImage(image)
    }

    fun setPreparingImage(preparing: Boolean) {
        _uiState.update { it.copy(preparingImage = preparing, error = null) }
    }

    fun showImageError(message: String) {
        _uiState.update { it.copy(preparingImage = false, error = message) }
    }

    fun removeImage(imageId: String) {
        imageUploadJobs.remove(imageId)?.cancel()
        val uploadedId = _uiState.value.pendingImages.firstOrNull { it.id == imageId }?.uploadedId
        _uiState.update { it.copy(pendingImages = it.pendingImages.filterNot { image -> image.id == imageId }) }
        NotificationHelper.cancelTransfer(getApplication(), imageId)
        if (uploadedId != null) viewModelScope.launch { runCatching { client?.deleteFile(uploadedId) } }
        saveCurrentDraft()
    }

    fun prepareFile(uri: Uri, storage: SelectedUriStorage = SelectedUriStorage.PERSISTED_URI) {
        viewModelScope.launch {
            runCatching { FileProcessor.prepare(getApplication(), uri, storage) }
                .onSuccess { localFile ->
                    val queued = localFile.copy(uploadState = AttachmentUploadState.Uploading(0))
                    _uiState.update { state ->
                        state.copy(
                            pendingFiles = state.pendingFiles + queued,
                            featurePanelOpen = false,
                            error = null
                        )
                    }
                    saveCurrentDraft()
                    uploadFile(queued)
                }
                .onFailure { error ->
                    _uiState.update { it.copy(error = error.message ?: "文件处理失败") }
                }
        }
    }

    private fun uploadFile(file: ChatFile) {
        val api = client ?: run {
            _uiState.update { state ->
                state.copy(
                    pendingFiles = state.pendingFiles.map { pending ->
                        if (pending.id == file.id) pending.copy(uploadState = AttachmentUploadState.Failed("尚未连接 Hermes")) else pending
                    }
                )
            }
            return
        }
        val app = getApplication<Application>()
        fileUploadJobs.remove(file.id)?.cancel()
        fileUploadJobs[file.id] = viewModelScope.launch {
            runCatching {
                val source = FileProcessor.uploadSource(getApplication(), file)
                api.uploadStream(source.name, source.mimeType, source.size, source.openStream) { progress ->
                    NotificationHelper.showTransfer(app, file.id, file.name, progress, uploading = true)
                    _uiState.update { state ->
                        state.copy(
                            pendingFiles = state.pendingFiles.map { pending ->
                                if (pending.id == file.id) pending.copy(
                                    uploadState = AttachmentUploadState.Uploading(progress)
                                ) else pending
                            }
                        )
                    }
                }
            }.onSuccess { uploaded ->
                fileUploadJobs.remove(file.id)
                NotificationHelper.finishTransfer(app, file.id, file.name, uploading = true, success = true)
                _uiState.update { state ->
                    state.copy(
                        pendingFiles = state.pendingFiles.map { pending ->
                            if (pending.id == file.id) pending.copy(
                                name = uploaded.name,
                                mimeType = uploaded.mimeType ?: pending.mimeType,
                                size = uploaded.size,
                                uploadedId = uploaded.id,
                                downloadUrl = uploaded.downloadUrl,
                                uploadState = AttachmentUploadState.Ready(uploaded.id)
                            ) else pending
                        }
                    )
                }
                saveCurrentDraft()
            }.onFailure { error ->
                if (error is kotlinx.coroutines.CancellationException) return@onFailure
                fileUploadJobs.remove(file.id)
                NotificationHelper.finishTransfer(app, file.id, file.name, uploading = true, success = false)
                _uiState.update { state ->
                    state.copy(
                        pendingFiles = state.pendingFiles.map { pending ->
                            if (pending.id == file.id) pending.copy(
                                uploadState = AttachmentUploadState.Failed(friendlyNetworkError(error))
                            ) else pending
                        }
                    )
                }
                saveCurrentDraft()
            }
        }
    }

    fun retryFileUpload(fileId: String) {
        val file = _uiState.value.pendingFiles.firstOrNull { it.id == fileId } ?: return
        fileUploadJobs.remove(fileId)?.cancel()
        _uiState.update { state ->
            state.copy(
                pendingFiles = state.pendingFiles.map { pending ->
                    if (pending.id == fileId) pending.copy(uploadState = AttachmentUploadState.Uploading(0)) else pending
                },
                error = null
            )
        }
        uploadFile(file)
    }

    fun removeFile(fileId: String) {
        fileUploadJobs.remove(fileId)?.cancel()
        val pending = _uiState.value.pendingFiles.firstOrNull { it.id == fileId } ?: return
        _uiState.update { state ->
            state.copy(pendingFiles = removeFileAttachment(state.pendingFiles, fileId))
        }
        NotificationHelper.cancelTransfer(getApplication(), fileId)
        pending.uploadedId?.let { uploadedId ->
            viewModelScope.launch { runCatching { client?.deleteFile(uploadedId) } }
        }
        saveCurrentDraft()
    }

    fun send() {
        val api = client ?: return
        val state = _uiState.value
        val originalInput = _input.value
        val originalDraftKey = draftKey()
        val text = originalInput.trim()
        val images = state.pendingImages
        val files = state.pendingFiles
        if (!canSendComposition(text, images.map(ChatImage::id), files.map(ChatFile::id)) || state.streaming) return
        if (files.any { !it.uploadState.readyToSend }) {
            _uiState.update { it.copy(error = "请等待所有文件上传完成") }
            return
        }
        if (images.any { !it.uploadState.readyToSend }) {
            _uiState.update { it.copy(error = "请等待图片上传完成") }
            return
        }

        _input.value = ""
        _uiState.update {
            it.copy(
                pendingImages = emptyList(),
                pendingFiles = emptyList(),
                streaming = true,
                runStoppable = true,
                thinking = true,
                toolStatus = null,
                answerStatus = AnswerStatus.THINKING,
                error = null
            )
        }
        streamJob = viewModelScope.launch {
            var originSessionId = state.activeSessionId
            var optimisticUserId: String? = null
            var optimisticAssistantId: String? = null
            originSessionId?.let(observedActiveRuns::add)
            runCatching {
                val wasDraft = state.activeSessionId == null
                val sessionId = state.activeSessionId ?: persistDraftSession()
                originSessionId = sessionId
                val firstFileName = files.firstOrNull()?.name
                if (wasDraft) {
                    val title = deriveSessionTitle(text, firstFileName, images.isNotEmpty())
                    val renamed = api.renameSession(sessionId, title)
                    _uiState.update { current ->
                        current.copy(sessions = current.sessions.map { if (it.id == sessionId) renamed else it })
                    }
                }
                observedActiveRuns += sessionId
                RunMonitorService.start(
                    getApplication(),
                    state.serverUrl,
                    state.token,
                    sessionId,
                    state.activeSession?.displayTitle ?: deriveSessionTitle(text, firstFileName, images.isNotEmpty())
                )
                val sentFiles = files.map { file ->
                    file.copy(downloadUrl = file.downloadUrl?.let { resolveDownloadUrl(state.serverUrl, it) })
                }
                val userMessage = optimisticUserMessage(text, images, sentFiles)
                val assistantId = UUID.randomUUID().toString()
                val assistantMessage = ChatMessage(assistantId, ChatRole.ASSISTANT, "")
                liveAssistantMessageId = assistantId
                stopRequestedSessionId = null
                optimisticUserId = userMessage.id
                optimisticAssistantId = assistantId
                _uiState.update { current ->
                    if (current.activeSessionId != sessionId) current
                    else {
                        val baseMessages = current.messages.filterNot { message ->
                            message.id.startsWith("run-snapshot-")
                        }
                        current.copy(messages = baseMessages + userMessage + assistantMessage)
                    }
                }
                val payload = chatAttachmentPayload(images, files)
                api.streamChat(
                    sessionId = sessionId,
                    message = text,
                    attachmentIds = payload.ids,
                    attachmentKinds = payload.kinds,
                    personaModel = state.selectedPersonaModelId,
                    inferenceModel = state.selectedInferenceModelId,
                    reasoningEffort = state.selectedReasoningEffort.wireValue
                ) { event -> handleStreamEvent(sessionId, assistantId, event) }
                connectionStore.saveMessageCache(sessionId, _uiState.value.messages)
            }.onSuccess {
                liveAssistantMessageId = null
                images.forEach { NotificationHelper.cancelTransfer(getApplication(), it.id) }
                files.forEach {
                    fileUploadJobs.remove(it.id)?.cancel()
                    NotificationHelper.cancelTransfer(getApplication(), it.id)
                }
                drafts = drafts.save(originalDraftKey, ComposerDraft())
                draftImages.remove(originalDraftKey)
                draftFiles.remove(originalDraftKey)
                persistAllDrafts()
                _uiState.update {
                    if (it.activeSessionId == originSessionId) {
                        it.copy(
                            pendingFiles = emptyList(),
                            streaming = false,
                            runStoppable = false,
                            thinking = false,
                            toolStatus = null,
                            uploadProgress = null
                        )
                    } else it
                }
                refreshSessions()
            }.onFailure { error ->
                _uiState.update { current ->
                    if (current.activeSessionId != originSessionId) current
                    else {
                        val stoppedByUser = error is kotlinx.coroutines.CancellationException && stopRequestedSessionId == originSessionId
                        val restore = error !is kotlinx.coroutines.CancellationException
                        val restoredInput = draftTextAfterRunFailure(_input.value, originalInput, restore)
                        _input.value = restoredInput
                        val restoredFiles = if (stoppedByUser) {
                            current.pendingFiles
                        } else {
                            (files + current.pendingFiles).distinctBy(ChatFile::id)
                        }
                        current.copy(
                            messages = messagesAfterSendTermination(
                                current.messages,
                                optimisticUserId,
                                optimisticAssistantId,
                                if (stoppedByUser) SendTermination.STOPPED_BY_USER else SendTermination.FAILED
                            ),
                            pendingImages = if (!stoppedByUser && current.pendingImages.isEmpty()) images else current.pendingImages,
                            pendingFiles = restoredFiles,
                            streaming = false,
                            runStoppable = false,
                            thinking = false,
                            toolStatus = null,
                            uploadProgress = null
                        )
                    }
                }
                if (error !is kotlinx.coroutines.CancellationException && _uiState.value.activeSessionId == originSessionId) {
                    saveCurrentDraft()
                    showTransientAnswerStatus(AnswerStatus.FAILED)
                    showError(error)
                }
            }
        }
    }

    fun stopStreaming() {
        val state = _uiState.value
        if (!state.streaming || !state.runStoppable) return
        val api = client
        val sessionId = state.activeSessionId
        stopRequestedSessionId = sessionId
        runStatusJob?.cancel()
        streamJob?.cancel()
        if (api != null && sessionId != null) {
            viewModelScope.launch {
                runCatching { api.stopSessionRun(sessionId) }
                delay(400)
                refreshActiveRunStatus()
            }
        }
        observedActiveRuns.remove(sessionId)
        showTransientAnswerStatus(AnswerStatus.STOPPED)
        _uiState.update {
            it.copy(
                streaming = false,
                runStoppable = false,
                thinking = false,
                toolStatus = null,
                uploadProgress = null
            )
        }
    }

    private fun showTransientAnswerStatus(status: AnswerStatus) {
        answerStatusJob?.cancel()
        _uiState.update { it.copy(answerStatus = status) }
        clearAnswerStatusLater(status)
    }

    private fun clearAnswerStatusLater(status: AnswerStatus) {
        answerStatusJob?.cancel()
        answerStatusJob = viewModelScope.launch {
            delay(2_500)
            _uiState.update { state -> if (state.answerStatus == status) state.copy(answerStatus = AnswerStatus.IDLE) else state }
        }
    }

    fun selectDownload(file: ChatFile) {
        val fileId = file.downloadUrl?.let(::gatewayFileId)
        val api = client
        if (file.size == 0L && file.mimeType == null && fileId != null && api != null) {
            viewModelScope.launch {
                runCatching { api.getFileMetadata(fileId) }
                    .onSuccess { resolved -> _uiState.update { it.copy(selectedDownload = resolved) } }
                    .onFailure { _uiState.update { it.copy(selectedDownload = file) } }
            }
        } else {
            _uiState.update { it.copy(selectedDownload = file) }
        }
    }

    fun openDownloadFromNotification(fileKey: String) {
        val file = _uiState.value.messages.asSequence()
            .flatMap { it.files.asSequence() }
            .firstOrNull { it.id == fileKey }
        if (file != null) {
            _uiState.update { it.copy(selectedDownload = file) }
            return
        }
        val api = client ?: return
        viewModelScope.launch {
            runCatching { api.getFileMetadata(fileKey) }
                .onSuccess { resolved -> _uiState.update { it.copy(selectedDownload = resolved) } }
        }
    }

    fun closeDownload() {
        _uiState.update { it.copy(selectedDownload = null) }
    }

    fun startDownload() {
        val file = _uiState.value.selectedDownload ?: return
        val url = file.downloadUrl ?: return
        val key = file.id
        val app = getApplication<Application>()
        downloadJobs[key]?.cancel()
        val generation = (downloadGenerations[key] ?: 0L) + 1L
        downloadGenerations[key] = generation
        val priorProgress = _uiState.value.downloadStates[key]?.progress ?: 0
        val sourceSessionId = _uiState.value.activeSessionId
        _uiState.update {
            it.copy(downloadStates = it.downloadStates + (key to FileDownloadState(key, DownloadStatus.DOWNLOADING, priorProgress, sessionId = sourceSessionId)))
        }
        downloadJobs[key] = viewModelScope.launch {
            runCatching {
                DownloadHelper.download(
                    getApplication(),
                    key,
                    url,
                    _uiState.value.token,
                    _uiState.value.serverUrl,
                    file.name
                ) { progress ->
                    if (downloadGenerations[key] != generation) return@download
                    if (progress < 100) NotificationHelper.showTransfer(app, key, file.name, progress, uploading = false, sessionId = sourceSessionId)
                    _uiState.update { state ->
                        if (downloadGenerations[key] != generation) state
                        else state.copy(downloadStates = state.downloadStates + (key to FileDownloadState(key, DownloadStatus.DOWNLOADING, progress, sessionId = sourceSessionId)))
                    }
                }
            }.onSuccess { local ->
                if (downloadGenerations[key] != generation) return@onSuccess
                NotificationHelper.finishTransfer(app, key, file.name, uploading = false, success = true, sessionId = sourceSessionId)
                _uiState.update { state ->
                    state.copy(downloadStates = state.downloadStates + (key to FileDownloadState(key, DownloadStatus.COMPLETED, 100, local.absolutePath, sessionId = sourceSessionId)))
                }
            }.onFailure { error ->
                if (downloadGenerations[key] != generation || error is kotlinx.coroutines.CancellationException) return@onFailure
                NotificationHelper.finishTransfer(app, key, file.name, uploading = false, success = false, sessionId = sourceSessionId)
                _uiState.update { state ->
                    state.copy(downloadStates = state.downloadStates + (key to FileDownloadState(key, DownloadStatus.FAILED, error = friendlyNetworkError(error), sessionId = sourceSessionId)))
                }
            }
        }
    }

    fun pauseDownload() {
        val file = _uiState.value.selectedDownload ?: return
        val current = _uiState.value.downloadStates[file.id] ?: return
        downloadGenerations[file.id] = (downloadGenerations[file.id] ?: 0L) + 1L
        downloadJobs.remove(file.id)?.cancel()
        DownloadHelper.pause(file.id)
        NotificationHelper.showPausedTransfer(getApplication(), file.id, file.name, current.progress, current.sessionId)
        _uiState.update {
            it.copy(downloadStates = it.downloadStates + (file.id to current.copy(status = DownloadStatus.PAUSED)))
        }
    }

    fun cancelDownload() {
        val file = _uiState.value.selectedDownload ?: return
        val state = _uiState.value.downloadStates[file.id]
        downloadGenerations[file.id] = (downloadGenerations[file.id] ?: 0L) + 1L
        downloadJobs.remove(file.id)?.cancel()
        val target = state?.let(::downloadTransferNotificationTarget)
            ?: TransferNotificationTarget(file.id, null)
        NotificationHelper.cancelTransfer(getApplication(), target.key, target.sessionId)
        DownloadHelper.cancel(getApplication(), file.id)
        _uiState.update { it.copy(downloadStates = it.downloadStates - file.id) }
    }

    fun openDownloadedFile() {
        val file = _uiState.value.selectedDownload ?: return
        val path = _uiState.value.downloadStates[file.id]?.localPath ?: return
        runCatching { DownloadHelper.openWithChooser(getApplication(), java.io.File(path), file.mimeType) }
            .onFailure { _uiState.update { state -> state.copy(error = "手机上没有能够打开此文件的应用") } }
    }

    fun deleteDownloadedFile() {
        val file = _uiState.value.selectedDownload ?: return
        val current = _uiState.value.downloadStates[file.id]
        DownloadHelper.deleteLocal(current?.localPath)
        _uiState.update { it.copy(downloadStates = it.downloadStates - file.id) }
    }

    fun setDrawerOpen(open: Boolean) {
        _uiState.update { it.copy(drawerOpen = open) }
    }

    fun toggleFeaturePanel() {
        _uiState.update { it.copy(featurePanelOpen = !it.featurePanelOpen) }
    }

    fun toggleVoiceInputMode() {
        _uiState.update { it.copy(voiceInputMode = !it.voiceInputMode, featurePanelOpen = false, voicePreview = "") }
    }

    fun setVoiceListening(listening: Boolean) {
        _uiState.update { it.copy(voiceListening = listening, voicePreview = if (listening) "正在聆听…" else it.voicePreview) }
    }

    fun updateVoicePreview(text: String) {
        _uiState.update { it.copy(voicePreview = text) }
    }

    fun acceptVoiceResult(text: String) {
        val transcript = voiceTranscriptState(text)
        _input.value = transcript.text
        _uiState.update {
            it.copy(
                voiceListening = false,
                voicePreview = "",
                voiceInputMode = false
            )
        }
    }

    fun showVoiceError(message: String) {
        _uiState.update { it.copy(voiceListening = false, voicePreview = "", error = message) }
    }

    fun showComingSoon(feature: String) {
        _uiState.update { it.copy(featurePanelOpen = false, error = "${feature}将在后续版本开放") }
    }

    fun openPersonaModelPicker() {
        openModelPicker(ModelPickerKind.PERSONA)
    }

    fun openInferenceModelPicker() {
        openModelPicker(ModelPickerKind.INFERENCE)
    }

    fun openReasoningPicker() {
        openModelPicker(ModelPickerKind.REASONING)
    }

    private fun openModelPicker(kind: ModelPickerKind) {
        _uiState.update { it.copy(modelPicker = kind, error = null) }
        val state = _uiState.value
        if (kind != ModelPickerKind.REASONING &&
            state.personaModels.isEmpty() &&
            state.inferenceModels.isEmpty() &&
            !state.modelsLoading
        ) {
            refreshModels()
        }
    }

    fun closeModelPicker() {
        _uiState.update { it.copy(modelPicker = null) }
    }

    fun refreshModels(showErrors: Boolean = true) {
        val api = client ?: return
        if (_uiState.value.modelsLoading) return
        val requestedRuntimeKey = runtimeKey()
        _uiState.update { it.copy(modelsLoading = true) }
        viewModelScope.launch {
            runCatching { api.listModels() }
                .onSuccess { models ->
                    val personas = personaModels(models)
                    val inference = inferenceModels(models)
                    val currentState = _uiState.value
                    val selectedPersona = resolveSelectedPersonaModelId(
                        personas,
                        currentState.selectedPersonaModelId
                    )
                    val requestedConfig = runtimeConfigs[requestedRuntimeKey] ?: ConversationRuntimeConfig()
                    val selectedInference = resolveSelectedInferenceModelId(
                        inference,
                        requestedConfig.inferenceModelId
                    )
                    connectionStore.saveSelectedPersonaModel(selectedPersona)
                    saveRuntimeConfig(
                        requestedRuntimeKey,
                        requestedConfig.copy(inferenceModelId = selectedInference)
                    )
                    _uiState.update { state ->
                        val stateRuntimeKey = runtimeKey(state.activeSessionId)
                        state.copy(
                            personaModels = personas,
                            selectedPersonaModelId = selectedPersona,
                            inferenceModels = inference,
                            selectedInferenceModelId = if (stateRuntimeKey == requestedRuntimeKey) {
                                selectedInference
                            } else {
                                runtimeConfig(state.activeSessionId).inferenceModelId
                            },
                            selectedReasoningEffort = runtimeConfig(state.activeSessionId).reasoningEffort,
                            modelsLoading = false
                        )
                    }
                }
                .onFailure { error ->
                    _uiState.update { it.copy(modelsLoading = false) }
                    if (showErrors) showError(error)
                }
        }
    }

    fun selectPersonaModel(modelId: String?) {
        if (modelId != null && _uiState.value.personaModels.none { it.id == modelId }) return
        connectionStore.saveSelectedPersonaModel(modelId)
        _uiState.update { it.copy(selectedPersonaModelId = modelId, error = null) }
    }

    fun selectInferenceModel(modelId: String?) {
        if (modelId != null && _uiState.value.inferenceModels.none { it.id == modelId }) return
        val key = runtimeKey()
        val updated = runtimeConfig().copy(inferenceModelId = modelId)
        saveRuntimeConfig(key, updated)
        _uiState.update { it.copy(selectedInferenceModelId = modelId, error = null) }
    }

    fun selectReasoningEffort(effort: ReasoningEffort) {
        val key = runtimeKey()
        val updated = runtimeConfig().copy(reasoningEffort = effort)
        saveRuntimeConfig(key, updated)
        _uiState.update { it.copy(selectedReasoningEffort = effort, error = null) }
    }

    fun openCronManager() {
        _uiState.update {
            it.copy(
                cronManagerOpen = true,
                cronNotice = null,
                error = null
            )
        }
        refreshCronJobs()
    }

    fun closeCronManager() {
        _uiState.update {
            it.copy(
                cronManagerOpen = false,
                cronEditor = null,
                cronJobToDelete = null,
                cronNotice = null,
                error = null
            )
        }
    }

    fun refreshCronJobs() {
        val api = client ?: return
        if (_uiState.value.cronJobsLoading) return
        _uiState.update { it.copy(cronJobsLoading = true, cronNotice = null) }
        viewModelScope.launch {
            runCatching { api.listCronJobs() }
                .onSuccess { jobs -> _uiState.update { it.copy(cronJobs = jobs, cronJobsLoading = false) } }
                .onFailure { error ->
                    _uiState.update { it.copy(cronJobsLoading = false) }
                    showError(error)
                }
        }
    }

    fun createCronJob() {
        _uiState.update { it.copy(cronEditor = CronJobEditorState.create(), cronNotice = null, error = null) }
    }

    fun editCronJob(job: HermesCronJob) {
        _uiState.update { it.copy(cronEditor = CronJobEditorState.edit(job), cronNotice = null, error = null) }
    }

    fun cancelCronEditor() {
        if (_uiState.value.cronBusyJobId != null) return
        _uiState.update { it.copy(cronEditor = null, error = null) }
    }

    fun updateCronJobName(value: String) = updateCronEditor { it.copy(name = value) }

    fun updateCronJobSchedule(value: String) = updateCronEditor { it.copy(schedule = value) }

    fun updateCronJobPrompt(value: String) = updateCronEditor { it.copy(prompt = value) }

    fun updateCronJobRepeat(value: String) = updateCronEditor { it.copy(repeatText = value) }

    fun updateCronJobEnabled(value: Boolean) = updateCronEditor { it.copy(enabled = value) }

    private fun updateCronEditor(transform: (CronJobEditorState) -> CronJobEditorState) {
        if (_uiState.value.cronBusyJobId != null) return
        _uiState.update { state ->
            state.copy(cronEditor = state.cronEditor?.let(transform), error = null)
        }
    }

    fun saveCronJob() {
        val api = client ?: return
        val editor = _uiState.value.cronEditor ?: return
        val name = editor.name.trim()
        val schedule = editor.schedule.trim()
        val prompt = editor.prompt.trim()
        when {
            name.isBlank() -> {
                _uiState.update { it.copy(error = "任务名称不能为空") }
                return
            }
            schedule.isBlank() -> {
                _uiState.update { it.copy(error = "执行计划不能为空") }
                return
            }
            prompt.isBlank() -> {
                _uiState.update { it.copy(error = "任务内容不能为空") }
                return
            }
            !isValidRepeatInput(editor.repeatText) -> {
                _uiState.update { it.copy(error = "重复次数必须是大于 0 的整数，留空表示一直执行") }
                return
            }
        }
        val busyKey = editor.key
        _uiState.update { it.copy(cronBusyJobId = busyKey, cronNotice = null, error = null) }
        viewModelScope.launch {
            runCatching {
                var saved = if (editor.jobId == null) {
                    api.createCronJob(name, schedule, prompt, repeatCountOrNull(editor.repeatText))
                } else {
                    api.updateCronJob(
                        jobId = editor.jobId,
                        name = name,
                        schedule = schedule,
                        prompt = prompt,
                        repeatTimes = repeatCountOrNull(editor.repeatText),
                        completedRuns = editor.completedRuns
                    )
                }
                saved = when {
                    editor.enabled && saved.isPaused -> api.resumeCronJob(saved.id)
                    !editor.enabled && !saved.isPaused -> api.pauseCronJob(saved.id)
                    else -> saved
                }
                saved
            }.onSuccess { job ->
                _uiState.update { state ->
                    state.copy(
                        cronJobs = upsertCronJob(state.cronJobs, job),
                        cronBusyJobId = null,
                        cronEditor = null,
                        cronNotice = if (editor.jobId == null) "定时任务已创建" else "定时任务已保存"
                    )
                }
            }.onFailure { error ->
                _uiState.update { it.copy(cronBusyJobId = null) }
                showError(error)
            }
        }
    }

    fun toggleCronJob(job: HermesCronJob) {
        val api = client ?: return
        if (_uiState.value.cronBusyJobId != null) return
        _uiState.update { it.copy(cronBusyJobId = job.id, cronNotice = null, error = null) }
        viewModelScope.launch {
            runCatching { if (job.isPaused) api.resumeCronJob(job.id) else api.pauseCronJob(job.id) }
                .onSuccess { updated ->
                    _uiState.update { state ->
                        state.copy(
                            cronJobs = upsertCronJob(state.cronJobs, updated),
                            cronBusyJobId = null,
                            cronNotice = if (updated.isPaused) "任务已暂停" else "任务已恢复"
                        )
                    }
                }
                .onFailure { error ->
                    _uiState.update { it.copy(cronBusyJobId = null) }
                    showError(error)
                }
        }
    }

    fun runCronJobNow(job: HermesCronJob) {
        val api = client ?: return
        if (_uiState.value.cronBusyJobId != null) return
        _uiState.update { it.copy(cronBusyJobId = job.id, cronNotice = null, error = null) }
        viewModelScope.launch {
            runCatching { api.runCronJob(job.id) }
                .onSuccess { updated ->
                    _uiState.update { state ->
                        state.copy(
                            cronJobs = upsertCronJob(state.cronJobs, updated),
                            cronBusyJobId = null,
                            cronNotice = "任务已提交执行"
                        )
                    }
                }
                .onFailure { error ->
                    _uiState.update { it.copy(cronBusyJobId = null) }
                    showError(error)
                }
        }
    }

    fun requestDeleteCronJob(job: HermesCronJob) {
        if (_uiState.value.cronBusyJobId != null) return
        _uiState.update { it.copy(cronJobToDelete = job, cronNotice = null, error = null) }
    }

    fun cancelDeleteCronJob() {
        if (_uiState.value.cronBusyJobId != null) return
        _uiState.update { it.copy(cronJobToDelete = null, error = null) }
    }

    fun confirmDeleteCronJob() {
        val api = client ?: return
        val job = _uiState.value.cronJobToDelete ?: return
        if (_uiState.value.cronBusyJobId != null) return
        _uiState.update { it.copy(cronBusyJobId = job.id, error = null) }
        viewModelScope.launch {
            runCatching {
                check(api.deleteCronJob(job.id)) { "服务器未删除该定时任务" }
            }
                .onSuccess {
                    _uiState.update { state ->
                        state.copy(
                            cronJobs = state.cronJobs.filterNot { it.id == job.id },
                            cronBusyJobId = null,
                            cronJobToDelete = null,
                            cronNotice = "定时任务已删除"
                        )
                    }
                }
                .onFailure { error ->
                    _uiState.update { it.copy(cronBusyJobId = null) }
                    showError(error)
                }
        }
    }

    private fun upsertCronJob(jobs: List<HermesCronJob>, job: HermesCronJob): List<HermesCronJob> =
        if (jobs.any { it.id == job.id }) jobs.map { if (it.id == job.id) job else it }
        else listOf(job) + jobs

    fun openSettings() {
        _uiState.update { it.copy(settingsOpen = true, drawerOpen = false) }
    }

    fun closeSettings() {
        _uiState.update { it.copy(settingsOpen = false) }
    }

    fun setAutoRefresh(enabled: Boolean) {
        connectionStore.saveAutoRefresh(enabled)
        _uiState.update { it.copy(autoRefresh = enabled) }
        if (enabled) startPolling() else stopPolling()
    }

    fun setThemeMode(mode: ThemeMode) {
        connectionStore.saveThemeMode(mode)
        _uiState.update { it.copy(themeMode = mode) }
    }

    fun logout() {
        stopPolling()
        draftPersistJob?.cancel()
        draftPersistJob = null
        performLocalLogoutCleanup(
            cancelStream = {
                streamJob?.cancel()
                streamJob = null
                sessionLoadJob?.cancel()
                sessionLoadJob = null
                runStatusJob?.cancel()
                runStatusJob = null
                answerStatusJob?.cancel()
                answerStatusJob = null
            },
            cancelUploads = {
                fileUploadJobs.values.forEach(Job::cancel)
                fileUploadJobs.clear()
                imageUploadJobs.values.forEach(Job::cancel)
                imageUploadJobs.clear()
            },
            cancelDownloads = {
                downloadJobs.values.forEach(Job::cancel)
                downloadJobs.clear()
                DownloadHelper.cancelAll()
            },
            cancelMonitors = { RunMonitorService.cancelAll(getApplication()) },
            cancelNotifications = { NotificationHelper.cancelAll(getApplication()) }
        )
        connectionStore.clear()
        client = null
        drafts = ConversationDrafts()
        runtimeConfigs.clear()
        draftImages.clear()
        draftFiles.clear()
        localDraftKey = "local-draft-${UUID.randomUUID()}"
        _input.value = ""
        _uiState.value = MainUiState()
    }

    fun closeFeaturePanel() {
        _uiState.update { it.copy(featurePanelOpen = false) }
    }

    fun clearError() {
        _uiState.update { it.copy(error = null) }
    }

    private fun handleStreamEvent(sessionId: String, assistantId: String, event: HermesStreamEvent) {
        _uiState.update { state ->
            if (state.activeSessionId != sessionId) return@update state
            when (event) {
                is HermesStreamEvent.TextDelta -> state.copy(
                    thinking = false,
                    answerStatus = AnswerStatus.GENERATING,
                    messages = state.messages.map { message ->
                        if (message.id == assistantId) message.copy(content = message.content + event.text) else message
                    }
                )
                is HermesStreamEvent.ToolStarted -> state.copy(
                    thinking = false,
                    toolStatus = "正在使用 ${event.name ?: "工具"}…",
                    answerStatus = AnswerStatus.TOOL
                )
                is HermesStreamEvent.ToolCompleted -> state.copy(toolStatus = "${event.name ?: "工具"}已完成", answerStatus = AnswerStatus.THINKING)
                is HermesStreamEvent.Error -> {
                    clearAnswerStatusLater(AnswerStatus.FAILED)
                    state.copy(thinking = false, answerStatus = AnswerStatus.FAILED, error = event.message)
                }
                HermesStreamEvent.Completed -> {
                    if (stopRequestedSessionId == sessionId) return@update state
                    observedActiveRuns.remove(sessionId)
                    clearAnswerStatusLater(AnswerStatus.COMPLETED)
                    state.copy(streaming = false, thinking = false, toolStatus = null, answerStatus = AnswerStatus.COMPLETED)
                }
                HermesStreamEvent.RunStarted -> state.copy(answerStatus = AnswerStatus.THINKING)
                HermesStreamEvent.StreamEnded -> state
            }
        }
    }

    private fun showError(error: Throwable) {
        val serverUrl = _uiState.value.serverUrl
        val message = friendlyNetworkError(error, serverUrl)
        if (requiresPasswordReauthentication(error)) {
            stopPolling()
            connectionStore.clearToken()
            client = null
            RunMonitorService.cancelAll(getApplication())
            _uiState.update {
                it.copy(
                    password = "",
                    token = "",
                    connectionStatus = ConnectionStatus.FAILED,
                    loading = false,
                    streaming = false,
                    thinking = false,
                    toolStatus = null,
                    error = message
                )
            }
        } else {
            _uiState.update { it.copy(error = message) }
        }
    }

    class Factory(
        private val application: Application
    ) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : androidx.lifecycle.ViewModel> create(modelClass: Class<T>): T =
            MainViewModel(application) as T
    }
}
