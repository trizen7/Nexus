package app.nexus.mobile

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import app.nexus.mobile.network.ChatMessage
import app.nexus.mobile.network.ChatFile
import app.nexus.mobile.network.ChatImage
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import java.util.Base64 as JavaBase64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class ConnectionStore(context: Context) {
    private val preferences = context.getSharedPreferences("nexus_connection", Context.MODE_PRIVATE)
    private val tokenCipher = TokenCipher()

    fun load(): SavedConnection {
        migratePersonaSelection()
        val storedServerUrl = preferences.getString(KEY_SERVER_URL, "").orEmpty()
        val migratedServerUrl = migrateStoredServerUrl(storedServerUrl)
        if (migratedServerUrl != storedServerUrl) {
            preferences.edit().putString(KEY_SERVER_URL, migratedServerUrl).apply()
        }
        return SavedConnection(
            serverUrl = migratedServerUrl,
            username = preferences.getString(KEY_USERNAME, "").orEmpty(),
            token = tokenCipher.decrypt(preferences.getString(KEY_TOKEN, "").orEmpty()),
            activeSessionId = preferences.getString(KEY_ACTIVE_SESSION, null),
            autoRefresh = preferences.getBoolean(KEY_AUTO_REFRESH, true),
            themeMode = ThemeMode.fromStored(preferences.getString(KEY_THEME_MODE, null)),
            selectedPersonaModelId = preferences.getString(KEY_SELECTED_PERSONA_MODEL, null)
                ?: preferences.getString(KEY_LEGACY_SELECTED_MODEL, null),
            selectedInferenceModelId = preferences.getString(KEY_SELECTED_INFERENCE_MODEL, null),
            selectedReasoningEffort = ReasoningEffort.fromStored(
                preferences.getString(KEY_SELECTED_REASONING_EFFORT, null)
            )
        )
    }

    fun saveLogin(serverUrl: String, username: String, token: String, activeSessionId: String?) {
        preferences.edit()
            .putString(KEY_SERVER_URL, serverUrl)
            .putString(KEY_USERNAME, username)
            .putString(KEY_TOKEN, tokenCipher.encrypt(token))
            .putString(KEY_ACTIVE_SESSION, activeSessionId)
            .remove(KEY_PASSWORD)
            .remove(KEY_LEGACY_TOKEN)
            .apply()
    }

    fun clearToken() {
        preferences.edit()
            .remove(KEY_TOKEN)
            .remove(KEY_LEGACY_TOKEN)
            .remove(KEY_PASSWORD)
            .apply()
    }

    fun saveActiveSession(activeSessionId: String?) {
        preferences.edit().putString(KEY_ACTIVE_SESSION, activeSessionId).apply()
    }

    fun saveAutoRefresh(enabled: Boolean) {
        preferences.edit().putBoolean(KEY_AUTO_REFRESH, enabled).apply()
    }

    fun saveThemeMode(mode: ThemeMode) {
        preferences.edit().putString(KEY_THEME_MODE, mode.name).apply()
    }

    fun saveSelectedPersonaModel(modelId: String?) {
        val editor = preferences.edit()
            .remove(KEY_LEGACY_SELECTED_MODEL)
            .putInt(KEY_PERSONA_SELECTION_SCHEMA, PERSONA_SELECTION_SCHEMA_VERSION)
        if (modelId.isNullOrBlank()) {
            editor.remove(KEY_SELECTED_PERSONA_MODEL)
        } else {
            editor.putString(KEY_SELECTED_PERSONA_MODEL, modelId)
        }
        editor.apply()
    }

    fun saveSelectedInferenceModel(modelId: String?) {
        val editor = preferences.edit()
        if (modelId.isNullOrBlank()) {
            editor.remove(KEY_SELECTED_INFERENCE_MODEL)
        } else {
            editor.putString(KEY_SELECTED_INFERENCE_MODEL, modelId)
        }
        editor.apply()
    }

    fun saveSelectedReasoningEffort(effort: ReasoningEffort) {
        val editor = preferences.edit()
        if (effort == ReasoningEffort.DEFAULT) {
            editor.remove(KEY_SELECTED_REASONING_EFFORT)
        } else {
            editor.putString(KEY_SELECTED_REASONING_EFFORT, effort.name)
        }
        editor.apply()
    }

    fun loadRuntimeConfigs(): PersistedRuntimeConfigBundle {
        val json = preferences.getString(KEY_RUNTIME_CONFIGS, null) ?: return PersistedRuntimeConfigBundle()
        return decodeRuntimeConfigBundle(json)
    }

    fun saveRuntimeConfigs(bundle: PersistedRuntimeConfigBundle) {
        preferences.edit().putString(KEY_RUNTIME_CONFIGS, encodeRuntimeConfigBundle(bundle)).apply()
    }

    fun clearLegacyRuntimeSelections() {
        preferences.edit()
            .remove(KEY_SELECTED_INFERENCE_MODEL)
            .remove(KEY_SELECTED_REASONING_EFFORT)
            .apply()
    }

    fun loadDrafts(): PersistedDraftBundle {
        val json = preferences.getString(KEY_DRAFTS, null) ?: return PersistedDraftBundle()
        return decodeDraftBundle(json)
    }

    fun saveDrafts(bundle: PersistedDraftBundle) {
        preferences.edit().putString(KEY_DRAFTS, encodeDraftBundle(bundle)).apply()
    }

    fun loadMessageCache(sessionId: String): List<ChatMessage> {
        val json = preferences.getString(cacheKey(sessionId), null) ?: return emptyList()
        return decodeMessageCache(json).takeLast(MESSAGE_CACHE_SIZE)
    }

    fun saveMessageCache(sessionId: String, messages: List<ChatMessage>) {
        preferences.edit().putString(cacheKey(sessionId), encodeMessageCache(messages.takeLast(MESSAGE_CACHE_SIZE))).apply()
    }

    fun clearMessageCache(sessionId: String) {
        preferences.edit().remove(cacheKey(sessionId)).apply()
    }

    fun clear() {
        preferences.edit().clear().apply()
    }

    private fun cacheKey(sessionId: String): String = messageCacheKey(sessionId)

    private fun migratePersonaSelection() {
        if (preferences.getInt(KEY_PERSONA_SELECTION_SCHEMA, 0) >= PERSONA_SELECTION_SCHEMA_VERSION) return
        preferences.edit()
            .remove(KEY_SELECTED_PERSONA_MODEL)
            .remove(KEY_LEGACY_SELECTED_MODEL)
            .putInt(KEY_PERSONA_SELECTION_SCHEMA, PERSONA_SELECTION_SCHEMA_VERSION)
            .apply()
    }

    private companion object {
        const val MESSAGE_CACHE_SIZE = 10
        const val KEY_SERVER_URL = "server_url"
        const val KEY_USERNAME = "username"
        const val KEY_PASSWORD = "password"
        const val KEY_TOKEN = "encrypted_token"
        const val KEY_LEGACY_TOKEN = "token"
        const val KEY_ACTIVE_SESSION = "active_session_id"
        const val KEY_AUTO_REFRESH = "auto_refresh"
        const val KEY_THEME_MODE = "theme_mode"
        const val KEY_LEGACY_SELECTED_MODEL = "selected_model_id"
        const val KEY_SELECTED_PERSONA_MODEL = "selected_persona_model_id"
        const val KEY_SELECTED_INFERENCE_MODEL = "selected_inference_model_id"
        const val KEY_SELECTED_REASONING_EFFORT = "selected_reasoning_effort"
        const val KEY_PERSONA_SELECTION_SCHEMA = "persona_selection_schema"
        const val PERSONA_SELECTION_SCHEMA_VERSION = 1
        const val KEY_RUNTIME_CONFIGS = "conversation_runtime_configs_v1"
        const val KEY_DRAFTS = "composer_drafts_v1"
    }
}

fun messageCacheKey(sessionId: String): String =
    "message_cache_v2_${JavaBase64.getUrlEncoder().withoutPadding().encodeToString(sessionId.toByteArray(StandardCharsets.UTF_8))}"

fun sessionIdFromMessageCacheKey(key: String): String? = runCatching {
    val encoded = key.removePrefix("message_cache_v2_")
    require(encoded != key)
    String(JavaBase64.getUrlDecoder().decode(encoded), StandardCharsets.UTF_8)
}.getOrNull()

const val RUNTIME_CONFIG_SCHEMA_VERSION = 1

data class PersistedRuntimeConfigBundle(
    val schemaVersion: Int = 0,
    val localDraftKey: String = "",
    val configs: Map<String, PersistedConversationRuntimeConfig> = emptyMap()
)

data class PersistedConversationRuntimeConfig(
    val inferenceModelId: String? = null,
    val reasoningEffort: String? = null
) {
    fun toRuntimeConfig(): ConversationRuntimeConfig = ConversationRuntimeConfig(
        inferenceModelId = inferenceModelId?.takeIf(String::isNotBlank),
        reasoningEffort = ReasoningEffort.fromStored(reasoningEffort)
    )
}

fun ConversationRuntimeConfig.toPersistedRuntimeConfig(): PersistedConversationRuntimeConfig =
    PersistedConversationRuntimeConfig(
        inferenceModelId = inferenceModelId?.takeIf(String::isNotBlank),
        reasoningEffort = reasoningEffort.takeUnless { it == ReasoningEffort.DEFAULT }?.name
    )

fun migrateRuntimeConfigBundle(
    bundle: PersistedRuntimeConfigBundle,
    activeSessionId: String?,
    localDraftKey: String,
    legacyInferenceModelId: String?,
    legacyReasoningEffort: ReasoningEffort
): PersistedRuntimeConfigBundle {
    val resolvedLocalDraftKey = localDraftKey.ifBlank { bundle.localDraftKey }
    if (bundle.schemaVersion >= RUNTIME_CONFIG_SCHEMA_VERSION) {
        return bundle.copy(localDraftKey = resolvedLocalDraftKey)
    }
    val configs = bundle.configs.toMutableMap()
    val legacyConfig = ConversationRuntimeConfig(
        inferenceModelId = legacyInferenceModelId?.takeIf(String::isNotBlank),
        reasoningEffort = legacyReasoningEffort
    )
    val targetKey = activeSessionId ?: resolvedLocalDraftKey
    if (!legacyConfig.isDefault && targetKey.isNotBlank() && targetKey !in configs) {
        configs[targetKey] = legacyConfig.toPersistedRuntimeConfig()
    }
    return PersistedRuntimeConfigBundle(
        schemaVersion = RUNTIME_CONFIG_SCHEMA_VERSION,
        localDraftKey = resolvedLocalDraftKey,
        configs = configs
    )
}

fun encodeRuntimeConfigBundle(bundle: PersistedRuntimeConfigBundle): String = Gson().toJson(bundle)

fun decodeRuntimeConfigBundle(json: String): PersistedRuntimeConfigBundle = runCatching {
    val type = object : TypeToken<PersistedRuntimeConfigBundle>() {}.type
    Gson().fromJson<PersistedRuntimeConfigBundle>(json, type) ?: PersistedRuntimeConfigBundle()
}.getOrDefault(PersistedRuntimeConfigBundle())

data class PersistedDraftBundle(
    val localDraftKey: String = "",
    val drafts: Map<String, PersistedComposerDraft> = emptyMap()
)

data class PersistedComposerDraft(
    val text: String = "",
    val images: List<PersistedDraftImage> = emptyList(),
    val files: List<PersistedDraftFile> = emptyList(),
    // Kept only to migrate drafts written by Nexus 0.0.8 and earlier.
    val file: PersistedDraftFile? = null
) {
    fun resolvedFiles(): List<PersistedDraftFile> = files.ifEmpty { listOfNotNull(file) }
}

data class PersistedDraftImage(
    val id: String,
    val previewUri: String,
    val uploadedId: String?
) {
    fun toImage(): ChatImage = ChatImage(
        id = id,
        previewUri = previewUri,
        dataUrl = previewUri,
        uploadedId = uploadedId,
        uploadState = uploadedId?.let { AttachmentUploadState.Ready(it) } ?: AttachmentUploadState.Local
    )
}

data class PersistedDraftFile(
    val id: String,
    val name: String,
    val mimeType: String?,
    val size: Long,
    val uri: String,
    val uploadedId: String?,
    val downloadUrl: String?
) {
    fun toFile(): ChatFile = ChatFile(
        id = id,
        name = name,
        mimeType = mimeType,
        size = size,
        uri = uri,
        uploadedId = uploadedId,
        downloadUrl = downloadUrl,
        uploadState = uploadedId?.let { AttachmentUploadState.Ready(it) } ?: AttachmentUploadState.Local
    )
}

fun encodeDraftBundle(bundle: PersistedDraftBundle): String = Gson().toJson(bundle)

fun decodeDraftBundle(json: String): PersistedDraftBundle = runCatching {
    val type = object : TypeToken<PersistedDraftBundle>() {}.type
    Gson().fromJson<PersistedDraftBundle>(json, type) ?: PersistedDraftBundle()
}.getOrDefault(PersistedDraftBundle())

private class TokenCipher {
    private val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    fun encrypt(value: String): String {
        if (value.isBlank()) return ""
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val payload = cipher.iv + cipher.doFinal(value.toByteArray(StandardCharsets.UTF_8))
        return android.util.Base64.encodeToString(payload, android.util.Base64.NO_WRAP)
    }

    fun decrypt(value: String): String = runCatching {
        if (value.isBlank()) return ""
        val payload = android.util.Base64.decode(value, android.util.Base64.NO_WRAP)
        if (payload.size <= IV_BYTES) return ""
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(128, payload.copyOfRange(0, IV_BYTES)))
        String(cipher.doFinal(payload.copyOfRange(IV_BYTES, payload.size)), StandardCharsets.UTF_8)
    }.getOrDefault("")

    private fun secretKey(): SecretKey = (keyStore.getKey(KEY_ALIAS, null) as? SecretKey) ?: KeyGenerator
        .getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        .apply {
            init(
                KeyGenParameterSpec.Builder(
                    KEY_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .build()
            )
        }
        .generateKey()

    private companion object {
        const val KEY_ALIAS = "nexus_device_token"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
    }
}
