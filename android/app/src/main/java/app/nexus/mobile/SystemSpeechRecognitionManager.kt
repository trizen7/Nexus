package app.nexus.mobile

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import android.speech.RecognitionService

internal data class SystemSpeechRecognitionService(
    val component: ComponentName,
    val label: String,
) {
    val id: String = component.flattenToString()
}

internal object SystemSpeechRecognitionManager {
    private const val PREFERENCES = "nexus_system_speech"
    private const val KEY_SELECTED_SERVICE = "selected_service"
    private const val VOICE_RECOGNITION_SERVICE = "voice_recognition_service"

    fun availableServices(context: Context): List<SystemSpeechRecognitionService> {
        val manager = context.packageManager
        val intent = Intent(RecognitionService.SERVICE_INTERFACE)
        val resolved = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            manager.queryIntentServices(
                intent,
                PackageManager.ResolveInfoFlags.of(PackageManager.MATCH_ALL.toLong()),
            )
        } else {
            @Suppress("DEPRECATION")
            manager.queryIntentServices(intent, PackageManager.MATCH_ALL)
        }
        return resolved.mapNotNull { info ->
            val service = info.serviceInfo ?: return@mapNotNull null
            val component = ComponentName(service.packageName, service.name)
            val label = runCatching { info.loadLabel(manager).toString().trim() }
                .getOrNull()
                .takeUnless { it.isNullOrBlank() }
                ?: service.packageName
            SystemSpeechRecognitionService(component, label)
        }.distinctBy { it.id }.sortedWith(compareBy({ it.label.lowercase() }, { it.id }))
    }

    fun defaultComponent(context: Context): ComponentName? = Settings.Secure.getString(
        context.contentResolver,
        VOICE_RECOGNITION_SERVICE,
    )?.let(ComponentName::unflattenFromString)

    fun selectedComponent(context: Context): ComponentName? {
        val selectedId = preferences(context).getString(KEY_SELECTED_SERVICE, null) ?: return null
        return availableServices(context).firstOrNull { it.id == selectedId }?.component
    }

    fun select(context: Context, component: ComponentName?) {
        preferences(context).edit().apply {
            if (component == null) remove(KEY_SELECTED_SERVICE)
            else putString(KEY_SELECTED_SERVICE, component.flattenToString())
        }.apply()
    }

    fun defaultLabel(context: Context, services: List<SystemSpeechRecognitionService>): String {
        val component = defaultComponent(context) ?: return "系统默认"
        return services.firstOrNull { it.component == component }?.label ?: component.packageName
    }

    private fun preferences(context: Context) =
        context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
}
