package app.nexus.mobile

import android.content.Context

internal enum class SpeechRecognitionEngine {
    SYSTEM,
    X_ASR,
    ONLINE,
}

internal object SpeechEnginePreferences {
    private const val PREFERENCES = "nexus_speech_engines"
    private const val KEY_INLINE_ENGINE = "inline_engine"
    private const val KEY_PHONE_ENGINE = "phone_engine"

    fun inline(context: Context): SpeechRecognitionEngine = read(context, KEY_INLINE_ENGINE)

    fun phone(context: Context): SpeechRecognitionEngine = read(context, KEY_PHONE_ENGINE)

    fun selectInline(context: Context, engine: SpeechRecognitionEngine) = write(context, KEY_INLINE_ENGINE, engine)

    fun selectPhone(context: Context, engine: SpeechRecognitionEngine) = write(context, KEY_PHONE_ENGINE, engine)

    fun resetXAsrSelections(context: Context) {
        preferences(context).edit().apply {
            if (inline(context) == SpeechRecognitionEngine.X_ASR) remove(KEY_INLINE_ENGINE)
            if (phone(context) == SpeechRecognitionEngine.X_ASR) remove(KEY_PHONE_ENGINE)
        }.apply()
    }

    private fun read(context: Context, key: String): SpeechRecognitionEngine =
        preferences(context).getString(key, null)
            ?.let { stored -> SpeechRecognitionEngine.entries.firstOrNull { it.name == stored } }
            ?: SpeechRecognitionEngine.SYSTEM

    private fun write(context: Context, key: String, engine: SpeechRecognitionEngine) {
        preferences(context).edit().putString(key, engine.name).apply()
    }

    private fun preferences(context: Context) =
        context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
}
