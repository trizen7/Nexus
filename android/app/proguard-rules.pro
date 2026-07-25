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
