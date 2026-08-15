# Gson persists these DTOs by reflected field name. Keep their serialized shape stable
# so release shrinking cannot break drafts, per-conversation settings, or message caches.
-keep class app.nexus.mobile.PersistedRuntimeConfigBundle { *; }
-keep class app.nexus.mobile.PersistedConversationRuntimeConfig { *; }
-keep class app.nexus.mobile.PersistedDraftBundle { *; }
-keep class app.nexus.mobile.PersistedComposerDraft { *; }
-keep class app.nexus.mobile.PersistedDraftImage { *; }
-keep class app.nexus.mobile.PersistedDraftFile { *; }
-keep class app.nexus.mobile.CachedImage { *; }
-keep class app.nexus.mobile.CachedFile { *; }
-keep class app.nexus.mobile.CachedMessage { *; }

# sherpa-onnx native JNI resolves Kotlin configuration classes and their fields
# by their original binary names (for example modelConfig and sampleRate). The
# upstream AAR does not currently ship consumer keep rules, so release R8 must
# preserve this package or X-ASR fails at runtime with "failed to get field ID".
-keep class com.k2fsa.sherpa.onnx.** { *; }
