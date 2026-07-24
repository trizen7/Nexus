package app.nexus.mobile.network

import com.google.gson.Gson
import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonNull
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okio.BufferedSink
import java.io.IOException
import java.io.InputStream
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketException
import java.net.SocketTimeoutException
import java.net.URI
import java.net.UnknownHostException
import java.security.cert.CertPathValidatorException
import java.security.cert.CertificateException
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLPeerUnverifiedException

class HermesApiClient(
    baseUrl: String,
    private var token: String = "",
    private val httpClient: OkHttpClient = defaultHttpClient()
) {
    val readTimeoutMillis: Int
        get() = httpClient.readTimeoutMillis
    private val baseUrl = baseUrl.trimEnd('/') + "/"
    private val jsonType = "application/json; charset=utf-8".toMediaType()
    private val gson = Gson()

    suspend fun login(username: String, password: String): String = withContext(Dispatchers.IO) {
        val payload = gson.toJson(mapOf("username" to username, "password" to password))
        val request = Request.Builder()
            .url(baseUrl + "api/auth/login")
            .post(payload.toRequestBody(jsonType))
            .build()
        httpClient.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw HermesHttpException(response.code, responseErrorMessage(text), "登录")
            }
            val root = gson.fromJson(text, JsonObject::class.java) ?: JsonObject()
            root.string("access_token").ifBlank { error("服务器没有返回登录凭证") }.also { token = it }
        }
    }

    suspend fun health(): HermesHealth = withContext(Dispatchers.IO) {
        val root = getJson("health")
        HermesHealth(
            status = root.string("status"),
            gatewayVersion = root.string("version"),
            hermesVersion = root.objectValue("upstream").string("version").takeIf { it.isNotBlank() }
        )
    }

    suspend fun listSessions(): List<HermesSession> = withContext(Dispatchers.IO) {
        val root = getJson("api/sessions")
        root.array("data").mapNotNull { element ->
            element.asJsonObjectOrNull()?.toSession()
        }
    }

    suspend fun listModels(): List<HermesModel> = withContext(Dispatchers.IO) {
        val root = getJson("v1/models")
        root.array("data").mapNotNull { element ->
            element.asJsonObjectOrNull()?.toModel()
        }
    }

    suspend fun listCronJobs(): List<HermesCronJob> = withContext(Dispatchers.IO) {
        val root = getJson("api/jobs?include_disabled=true")
        root.array("jobs").mapNotNull { element ->
            element.asJsonObjectOrNull()?.toCronJob()
        }
    }

    suspend fun createSession(): HermesSession = withContext(Dispatchers.IO) {
        val payload = "{}"
        val root = requestJson(
            Request.Builder()
                .url(baseUrl + "api/sessions")
                .post(payload.toRequestBody(jsonType))
        )
        val session = root.objectValue("session")
        session.toSession() ?: error("服务器没有返回有效会话")
    }

    suspend fun renameSession(sessionId: String, title: String): HermesSession = withContext(Dispatchers.IO) {
        val payload = gson.toJson(mapOf("title" to title))
        val root = requestJson(
            Request.Builder()
                .url(baseUrl + "api/sessions/${encodePathSegment(sessionId)}")
                .patch(payload.toRequestBody(jsonType))
        )
        root.objectValue("session").toSession() ?: error("服务器没有返回有效会话")
    }

    suspend fun deleteSession(sessionId: String): Boolean = withContext(Dispatchers.IO) {
        val root = requestJson(
            Request.Builder()
                .url(baseUrl + "api/sessions/${encodePathSegment(sessionId)}")
                .delete()
        )
        root.booleanValue("deleted")
    }

    suspend fun createCronJob(
        name: String,
        schedule: String,
        prompt: String,
        repeatTimes: Int?
    ): HermesCronJob = withContext(Dispatchers.IO) {
        val payload = JsonObject().apply {
            addProperty("name", name)
            addProperty("schedule", schedule)
            addProperty("prompt", prompt)
            addProperty("deliver", "local")
            repeatTimes?.let { addProperty("repeat", it) }
        }
        val root = requestJson(
            Request.Builder()
                .url(baseUrl + "api/jobs")
                .post(gson.toJson(payload).toRequestBody(jsonType))
        )
        root.objectValue("job").toCronJob() ?: error("服务器没有返回有效定时任务")
    }

    suspend fun updateCronJob(
        jobId: String,
        name: String,
        schedule: String,
        prompt: String,
        repeatTimes: Int?,
        completedRuns: Int
    ): HermesCronJob = withContext(Dispatchers.IO) {
        val repeatPayload = JsonObject().apply {
            if (repeatTimes == null) add("times", JsonNull.INSTANCE) else addProperty("times", repeatTimes)
            addProperty("completed", completedRuns)
        }
        val payload = JsonObject().apply {
            addProperty("name", name)
            addProperty("schedule", schedule)
            addProperty("prompt", prompt)
            add("repeat", repeatPayload)
        }
        val root = requestJson(
            Request.Builder()
                .url(baseUrl + "api/jobs/${encodePathSegment(jobId)}")
                .patch(gson.toJson(payload).toRequestBody(jsonType))
        )
        root.objectValue("job").toCronJob() ?: error("服务器没有返回有效定时任务")
    }

    suspend fun deleteCronJob(jobId: String): Boolean = withContext(Dispatchers.IO) {
        requestJson(
            Request.Builder()
                .url(baseUrl + "api/jobs/${encodePathSegment(jobId)}")
                .delete()
        ).booleanValue("ok")
    }

    suspend fun pauseCronJob(jobId: String): HermesCronJob = mutateCronJob(jobId, "pause")

    suspend fun resumeCronJob(jobId: String): HermesCronJob = mutateCronJob(jobId, "resume")

    suspend fun runCronJob(jobId: String): HermesCronJob = mutateCronJob(jobId, "run")

    private suspend fun mutateCronJob(jobId: String, action: String): HermesCronJob = withContext(Dispatchers.IO) {
        val root = requestJson(
            Request.Builder()
                .url(baseUrl + "api/jobs/${encodePathSegment(jobId)}/$action")
                .post("{}".toRequestBody(jsonType))
        )
        root.objectValue("job").toCronJob() ?: error("服务器没有返回有效定时任务")
    }

    suspend fun deleteFile(fileId: String): Boolean = withContext(Dispatchers.IO) {
        val root = requestJson(
            Request.Builder()
                .url(baseUrl + "api/files/${encodePathSegment(fileId)}")
                .delete()
        )
        root.booleanValue("deleted")
    }

    suspend fun getFileMetadata(fileId: String): ChatFile = withContext(Dispatchers.IO) {
        val root = getJson("api/files/${encodePathSegment(fileId)}/metadata")
        val file = root.objectValue("file")
        val id = file.string("id").ifBlank { fileId }
        ChatFile(
            id = id,
            name = file.string("name").ifBlank { "附件" },
            mimeType = file.string("mime_type").ifBlank { null },
            size = file.longValue("size"),
            uri = "",
            uploadedId = id,
            downloadUrl = resolveUrl(file.string("download_url"))
        )
    }

    suspend fun loadMessages(sessionId: String): List<ChatMessage> = loadMessagePage(sessionId, 100, 0).messages

    suspend fun getSessionRunStatus(sessionId: String): SessionRunStatus = withContext(Dispatchers.IO) {
        val root = getJson("api/sessions/${encodePathSegment(sessionId)}/run")
        val active = root.get("active")?.takeUnless { it.isJsonNull }?.asBoolean ?: false
        SessionRunStatus(
            sessionId = root.string("session_id").ifBlank { sessionId },
            runId = root.string("run_id").takeIf { it.isNotBlank() },
            status = root.string("status").ifBlank { "idle" },
            active = active,
            phase = root.string("phase").ifBlank { "idle" },
            snapshot = root.string("snapshot"),
            toolName = root.string("tool_name").takeIf { it.isNotBlank() },
            message = root.string("message").takeIf { it.isNotBlank() },
            source = root.string("source").ifBlank { "nexus_gateway" },
            stoppable = root.get("stoppable")?.takeUnless { it.isJsonNull }?.asBoolean ?: active
        )
    }

    suspend fun stopSessionRun(sessionId: String): Boolean = withContext(Dispatchers.IO) {
        val request = authorizedRequest(baseUrl + "api/sessions/${encodePathSegment(sessionId)}/run/stop")
            .post(ByteArray(0).toRequestBody(null))
            .build()
        httpClient.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw HermesHttpException(response.code, responseErrorMessage(text), "停止回答")
            }
            val root = gson.fromJson(text, JsonObject::class.java) ?: JsonObject()
            root.get("stopped")?.takeUnless { it.isJsonNull }?.asBoolean ?: false
        }
    }

    suspend fun loadMessagePage(sessionId: String, limit: Int, offset: Int): MessagePage = withContext(Dispatchers.IO) {
        val safeLimit = limit.coerceIn(1, 100)
        val safeOffset = offset.coerceAtLeast(0)
        val root = getJson("api/sessions/${encodePathSegment(sessionId)}/messages?limit=$safeLimit&offset=$safeOffset")
        val messages = root.array("data").mapIndexedNotNull { index, element ->
            val item = element.asJsonObjectOrNull() ?: return@mapIndexedNotNull null
            val (content, inlineImages) = item.contentAndImages()
            val persistedImages = item.array("nexus_images").mapIndexedNotNull imageMap@ { imageIndex, imageElement ->
                val image = imageElement.asJsonObjectOrNull() ?: return@imageMap null
                val url = image.string("url")
                if (url.isBlank()) return@imageMap null
                val resolvedUrl = resolveUrl(url)
                ChatImage(
                    id = image.string("id").ifBlank { "history-$index-$imageIndex" },
                    previewUri = resolvedUrl,
                    dataUrl = resolvedUrl
                )
            }
            val images = inlineImages + persistedImages
            val persistedFiles = item.array("nexus_files").mapIndexedNotNull fileMap@ { fileIndex, fileElement ->
                val file = fileElement.asJsonObjectOrNull() ?: return@fileMap null
                val url = file.string("url")
                if (url.isBlank()) return@fileMap null
                ChatFile(
                    id = file.string("id").ifBlank { "history-file-$index-$fileIndex" },
                    name = file.string("name").ifBlank { "附件" },
                    mimeType = file.string("mime_type").ifBlank { null },
                    size = file.longValue("size"),
                    uri = "",
                    uploadedId = file.string("id").ifBlank { null },
                    downloadUrl = resolveUrl(url)
                )
            }
            val role = when (item.string("role").lowercase()) {
                "user" -> ChatRole.USER
                "assistant" -> ChatRole.ASSISTANT
                "tool" -> ChatRole.TOOL
                else -> ChatRole.OTHER
            }
            if ((content.isBlank() && images.isEmpty() && persistedFiles.isEmpty()) || role == ChatRole.TOOL ||
                (role == ChatRole.USER && !app.nexus.mobile.isVisibleUserMessage(content))
            ) return@mapIndexedNotNull null
            ChatMessage(
                id = item.string("id").ifBlank { "$sessionId-${safeOffset + index}" },
                role = role,
                content = app.nexus.mobile.cleanScreenshotMarker(content, images.isNotEmpty()),
                images = images,
                files = persistedFiles
            )
        }
        val pagination = root.objectValue("pagination")
        MessagePage(
            messages = messages,
            total = pagination.intValue("total").takeIf { it > 0 } ?: messages.size,
            offset = pagination.intValue("offset"),
            limit = pagination.intValue("limit").takeIf { it > 0 } ?: safeLimit,
            hasMore = pagination.booleanValue("has_more")
        )
    }

    suspend fun transcribeAudio(name: String, content: ByteArray): TranscribedAudio = withContext(Dispatchers.IO) {
        val multipart = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("file", name, content.toRequestBody("audio/mp4".toMediaType()))
            .build()
        val root = requestJson(
            Request.Builder()
                .url(baseUrl + "api/audio/transcriptions")
                .post(multipart)
        )
        val file = root.objectValue("file")
        TranscribedAudio(
            transcript = root.string("transcript"),
            file = UploadedFile(
                id = file.string("id").ifBlank { error("网关没有返回录音文件 ID") },
                name = file.string("name").ifBlank { name },
                mimeType = file.string("mime_type").ifBlank { "audio/mp4" },
                size = file.longValue("size"),
                downloadUrl = file.string("download_url")
            )
        )
    }

    suspend fun uploadStream(
        name: String,
        mimeType: String?,
        size: Long,
        openStream: () -> InputStream,
        onProgress: (Int) -> Unit = {}
    ): UploadedFile = withContext(Dispatchers.IO) {
        val fileType = (mimeType ?: "application/octet-stream").toMediaType()
        val body = object : RequestBody() {
            override fun contentType() = fileType
            override fun contentLength() = size
            override fun writeTo(sink: BufferedSink) {
                openStream().use { input ->
                    val buffer = ByteArray(256 * 1024)
                    var sent = 0L
                    var lastProgress = -5
                    while (true) {
                        if (Thread.currentThread().isInterrupted) throw java.io.InterruptedIOException("上传已取消")
                        val count = input.read(buffer)
                        if (count < 0) break
                        sink.write(buffer, 0, count)
                        sent += count
                        val progress = if (size > 0) ((sent * 100) / size).toInt().coerceIn(0, 100) else 0
                        if (progress >= lastProgress + 5 || progress == 100) {
                            lastProgress = progress
                            onProgress(progress)
                        }
                    }
                    if (lastProgress < 100) onProgress(100)
                }
            }
        }
        val multipart = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("file", name, body)
            .build()
        parseUploadedFile(
            requestJson(Request.Builder().url(baseUrl + "api/uploads").post(multipart)),
            name,
            mimeType
        )
    }

    suspend fun uploadFile(
        name: String,
        mimeType: String?,
        content: ByteArray
    ): UploadedFile = withContext(Dispatchers.IO) {
        val fileType = (mimeType ?: "application/octet-stream").toMediaType()
        val multipart = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("file", name, content.toRequestBody(fileType))
            .build()
        parseUploadedFile(
            requestJson(
                Request.Builder()
                    .url(baseUrl + "api/uploads")
                    .post(multipart)
            ),
            name,
            mimeType
        )
    }

    suspend fun streamChat(
        sessionId: String,
        message: String,
        images: List<ChatImage> = emptyList(),
        attachmentIds: List<String> = emptyList(),
        attachmentKinds: Map<String, String> = emptyMap(),
        personaModel: String? = null,
        inferenceModel: String? = null,
        reasoningEffort: String? = null,
        onEvent: (HermesStreamEvent) -> Unit = {}
    ): List<HermesStreamEvent> = withContext(Dispatchers.IO) {
        val userContent: Any = if (images.isEmpty()) {
            message
        } else {
            buildList<Map<String, Any>> {
                if (message.isNotBlank()) add(mapOf("type" to "text", "text" to message))
                images.forEach { image ->
                    add(
                        mapOf(
                            "type" to "image_url",
                            "image_url" to mapOf("url" to image.dataUrl)
                        )
                    )
                }
            }
        }
        val body = mutableMapOf<String, Any>(
            "message" to userContent,
            "client_context" to mapOf(
                "platform" to "android",
                "form_factor" to "phone",
                "supports_direct_local_paths" to false,
                "supports_drag_and_drop" to false
            )
        )
        if (attachmentIds.isNotEmpty()) body["attachment_ids"] = attachmentIds
        if (attachmentKinds.isNotEmpty()) body["attachment_kinds"] = attachmentKinds
        personaModel?.trim()?.takeIf { it.isNotEmpty() }?.let { body["persona_model"] = it }
        inferenceModel?.trim()?.takeIf { it.isNotEmpty() }?.let { body["inference_model"] = it }
        reasoningEffort?.trim()?.takeIf { it.isNotEmpty() }?.let { body["reasoning_effort"] = it }
        val payload = gson.toJson(body)
        val request = authorizedRequest(
            baseUrl + "api/sessions/${encodePathSegment(sessionId)}/chat/stream"
        ).post(payload.toRequestBody(jsonType)).build()

        var emittedContent = false
        var attempt = 0
        var result: List<HermesStreamEvent>? = null
        while (result == null) {
            try {
                result = executeStreamRequest(request) { event ->
                    if (event is HermesStreamEvent.TextDelta || event is HermesStreamEvent.ToolStarted) {
                        emittedContent = true
                    }
                    onEvent(event)
                }
            } catch (error: IOException) {
                if (attempt >= 1 || emittedContent || !isConnectionAbort(error)) throw error
                attempt += 1
            }
        }
        result
    }

    private fun executeStreamRequest(
        request: Request,
        onEvent: (HermesStreamEvent) -> Unit
    ): List<HermesStreamEvent> = httpClient.newCall(request).execute().use { response ->
        if (!response.isSuccessful) {
            val text = response.body?.string().orEmpty()
            throw HermesHttpException(response.code, responseErrorMessage(text), "对话请求")
        }
        val parser = HermesStreamParser()
        val events = mutableListOf<HermesStreamEvent>()
        val source = response.body?.source() ?: error("响应内容为空")
        while (!source.exhausted()) {
            parser.accept(source.readUtf8Line().orEmpty())?.let { event ->
                events += event
                onEvent(event)
            }
        }
        parser.flush()?.let { event ->
            events += event
            onEvent(event)
        }
        events.distinctConsecutiveCompleted()
    }

    private fun parseUploadedFile(root: JsonObject, fallbackName: String, fallbackMime: String?): UploadedFile {
        val file = root.objectValue("file")
        return UploadedFile(
            id = file.string("id").ifBlank { error("网关没有返回文件 ID") },
            name = file.string("name").ifBlank { fallbackName },
            mimeType = file.string("mime_type").ifBlank { fallbackMime },
            size = file.longValue("size"),
            downloadUrl = file.string("download_url")
        )
    }

    private fun getJson(relativePath: String): JsonObject = requestJson(
        Request.Builder().url(baseUrl + relativePath).get()
    )

    private fun requestJson(builder: Request.Builder): JsonObject {
        val request = authorized(builder).build()
        httpClient.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw HermesHttpException(response.code, responseErrorMessage(text), "Hermes 请求")
            }
            return gson.fromJson(text, JsonObject::class.java) ?: JsonObject()
        }
    }

    private fun responseErrorMessage(text: String): String? = runCatching {
        val root = gson.fromJson(text, JsonObject::class.java) ?: return@runCatching null
        root.objectValue("error").string("message").ifBlank { root.string("message") }.ifBlank { null }
    }.getOrNull()

    private fun authorizedRequest(url: String): Request.Builder = authorized(Request.Builder().url(url))

    private fun authorized(builder: Request.Builder): Request.Builder =
        builder
            .header("Authorization", "Bearer $token")
            .header("Accept", "application/json, text/event-stream")

    private fun resolveUrl(url: String): String =
        if (url.startsWith("http://") || url.startsWith("https://")) url
        else baseUrl.trimEnd('/') + "/" + url.trimStart('/')

    private fun encodePathSegment(value: String): String =
        java.net.URLEncoder.encode(value, Charsets.UTF_8.name()).replace("+", "%20")

    private fun isConnectionAbort(error: IOException): Boolean =
        generateSequence<Throwable>(error) { it.cause }
            .any { cause ->
                cause is SocketException &&
                    cause.message.orEmpty().contains("connection abort", ignoreCase = true)
            }
}

data class HermesHealth(
    val status: String,
    val gatewayVersion: String,
    val hermesVersion: String?
)

class HermesHttpException(
    val statusCode: Int,
    val serverMessage: String?,
    val operation: String
) : IOException(serverMessage?.takeIf { it.isNotBlank() } ?: "$operation 失败：HTTP $statusCode")

private fun throwableChain(error: Throwable): Sequence<Throwable> =
    generateSequence(error) { it.cause }

fun requiresPasswordReauthentication(error: Throwable): Boolean =
    throwableChain(error)
        .filterIsInstance<HermesHttpException>()
        .any { it.statusCode == 401 && it.operation != "登录" }

private fun isConnectionAbort(error: Throwable): Boolean =
    throwableChain(error).any { cause ->
        cause is SocketException && cause.message.orEmpty().let { message ->
            message.contains("connection abort", ignoreCase = true) ||
                message.contains("connection reset", ignoreCase = true)
        }
    }

private fun certificateConnectionMessage(serverUrl: String?): String {
    val uri = serverUrl?.let { runCatching { URI(it) }.getOrNull() }
    return if (uri?.scheme.equals("https", ignoreCase = true)) {
        "HTTPS 安全连接失败，请检查反向代理证书是否有效、受信任且与服务器域名匹配"
    } else {
        "安全连接失败，请检查服务器地址和网络配置"
    }
}


fun friendlyNetworkError(error: Throwable, serverUrl: String? = null): String {
    val causes = throwableChain(error).toList()
    val httpError = causes.filterIsInstance<HermesHttpException>().firstOrNull()
    return when {
        error is kotlinx.coroutines.CancellationException -> "操作已取消"
        httpError?.statusCode == 401 && httpError.operation == "登录" -> "登录失败：账号或密码错误"
        httpError?.statusCode == 401 -> "登录已失效，请重新输入密码"
        httpError != null -> httpError.serverMessage?.takeIf { it.isNotBlank() }
            ?: "${httpError.operation}失败：HTTP ${httpError.statusCode}"
        causes.any { it is SSLHandshakeException || it is SSLPeerUnverifiedException ||
            it is CertPathValidatorException || it is CertificateException } -> certificateConnectionMessage(serverUrl)
        causes.any { it is UnknownHostException } ->
            "找不到服务器，请检查地址；局域网直连地址通常为 http://电脑局域网IP:18787"
        causes.any { it is ConnectException || it is NoRouteToHostException } ->
            "无法连接服务器，请确认电脑和手机在同一网络，并检查 IP、端口及 Gateway 是否运行"
        causes.any { it is SocketTimeoutException } ->
            "连接服务器超时，请检查局域网、IP 地址和端口"
        isConnectionAbort(error) -> app.nexus.mobile.genericConnectionInterruptedMessage()
        error is IllegalArgumentException ->
            "服务器地址格式不正确，请填写例如 http://10.0.0.123:18787 或 https://你的域名"
        error is IOException -> "网络连接异常，请稍后重试"
        else -> error.message ?: "发生未知错误"
    }
}

private fun defaultHttpClient(): OkHttpClient =
    OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.MINUTES)
        .writeTimeout(30, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .pingInterval(30, TimeUnit.SECONDS)
        .build()

private fun JsonObject.contentAndImages(): Pair<String, List<ChatImage>> {
    val contentElement = get("content")
    if (contentElement == null || contentElement.isJsonNull) return "" to emptyList()
    if (contentElement.isJsonPrimitive) return contentElement.asString to emptyList()
    if (!contentElement.isJsonArray) return "" to emptyList()

    val text = mutableListOf<String>()
    val images = mutableListOf<ChatImage>()
    contentElement.asJsonArray.forEachIndexed { index, element ->
        val part = element.asJsonObjectOrNull() ?: return@forEachIndexed
        when (part.string("type")) {
            "text", "input_text" -> part.string("text").takeIf { it.isNotBlank() }?.let(text::add)
            "image_url", "input_image" -> {
                val imageValue = part.get("image_url")
                val url = when {
                    imageValue == null || imageValue.isJsonNull -> ""
                    imageValue.isJsonPrimitive -> imageValue.asString
                    imageValue.isJsonObject -> imageValue.asJsonObject.string("url")
                    else -> ""
                }
                if (url.isNotBlank()) images += ChatImage("history-$index", url, url)
            }
        }
    }
    return text.joinToString("\n") to images
}

private fun JsonObject.toSession(): HermesSession? {
    val id = string("id")
    if (id.isBlank()) return null
    return HermesSession(
        id = id,
        title = get("title")?.takeUnless { it.isJsonNull }?.asString,
        source = string("source"),
        messageCount = intValue("message_count"),
        lastActive = doubleValue("last_active")
    )
}

private fun JsonObject.toModel(): HermesModel? {
    val id = string("id")
    if (id.isBlank()) return null
    return HermesModel(
        id = id,
        root = string("root").ifBlank { null },
        ownedBy = string("owned_by").ifBlank { null },
        parent = string("parent").ifBlank { null },
        kind = string("kind").ifBlank { null },
        objectType = string("object").ifBlank { null }
    )
}

private fun JsonObject.toCronJob(): HermesCronJob? {
    val id = string("id")
    if (id.isBlank()) return null
    val scheduleElement = get("schedule")
    val fallbackDisplay = string("schedule_display").ifBlank { null }
    val schedule = when {
        scheduleElement?.isJsonObject == true -> scheduleElement.asJsonObject.toCronSchedule(fallbackDisplay)
        scheduleElement?.isJsonPrimitive == true -> HermesCronSchedule(
            expression = scheduleElement.asString,
            display = fallbackDisplay ?: scheduleElement.asString
        )
        else -> HermesCronSchedule(display = fallbackDisplay)
    }
    val repeatElement = get("repeat")
    val repeatTimes = when {
        repeatElement?.isJsonObject == true -> repeatElement.asJsonObject.nullableInt("times")
        repeatElement?.isJsonPrimitive == true -> runCatching { repeatElement.asInt }.getOrNull()
        else -> null
    }
    val completedRuns = if (repeatElement?.isJsonObject == true) {
        repeatElement.asJsonObject.intValue("completed")
    } else {
        0
    }
    val enabled = get("enabled")
        ?.takeUnless { it.isJsonNull }
        ?.let { runCatching { it.asBoolean }.getOrNull() }
        ?: true
    return HermesCronJob(
        id = id,
        name = string("name").ifBlank { id },
        prompt = string("prompt"),
        schedule = schedule,
        repeatTimes = repeatTimes,
        completedRuns = completedRuns,
        enabled = enabled,
        state = string("state").ifBlank { if (enabled) "scheduled" else "paused" },
        nextRunAt = string("next_run_at").ifBlank { null },
        lastRunAt = string("last_run_at").ifBlank { null },
        lastStatus = string("last_status").ifBlank { null },
        lastError = string("last_error").ifBlank { null }
    )
}

private fun JsonObject.toCronSchedule(fallbackDisplay: String?): HermesCronSchedule = HermesCronSchedule(
    kind = string("kind"),
    expression = string("expr"),
    runAt = string("run_at").ifBlank { null },
    minutes = nullableInt("minutes"),
    display = string("display").ifBlank { fallbackDisplay }
)

private fun JsonObject.string(key: String): String =
    get(key)?.takeUnless { it.isJsonNull }?.let { runCatching { it.asString }.getOrNull() }.orEmpty()

private fun JsonObject.intValue(key: String): Int =
    get(key)?.takeUnless { it.isJsonNull }?.let { runCatching { it.asInt }.getOrNull() } ?: 0

private fun JsonObject.nullableInt(key: String): Int? =
    get(key)?.takeUnless { it.isJsonNull }?.let { runCatching { it.asInt }.getOrNull() }

private fun JsonObject.doubleValue(key: String): Double =
    get(key)?.takeUnless { it.isJsonNull }?.let { runCatching { it.asDouble }.getOrNull() } ?: 0.0

private fun JsonObject.longValue(key: String): Long =
    get(key)?.takeUnless { it.isJsonNull }?.let { runCatching { it.asLong }.getOrNull() } ?: 0L

private fun JsonObject.booleanValue(key: String): Boolean =
    get(key)?.takeUnless { it.isJsonNull }?.let { runCatching { it.asBoolean }.getOrNull() } ?: false

private fun JsonObject.array(key: String): JsonArray =
    get(key)?.takeIf { it.isJsonArray }?.asJsonArray ?: JsonArray()

private fun JsonObject.objectValue(key: String): JsonObject =
    get(key)?.takeIf { it.isJsonObject }?.asJsonObject ?: JsonObject()

private fun JsonElement.asJsonObjectOrNull(): JsonObject? =
    takeIf { it.isJsonObject }?.asJsonObject

private fun List<HermesStreamEvent>.distinctConsecutiveCompleted(): List<HermesStreamEvent> =
    fold(mutableListOf()) { result, event ->
        if (event !is HermesStreamEvent.Completed || result.lastOrNull() !is HermesStreamEvent.Completed) {
            result += event
        }
        result
    }
