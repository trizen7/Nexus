import java.net.URI as JavaUri
import java.nio.file.AtomicMoveNotSupportedException as JavaAtomicMoveNotSupportedException
import java.nio.file.Files as JavaFiles
import java.nio.file.StandardCopyOption as JavaCopyOption
import java.security.MessageDigest as JavaMessageDigest

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val sherpaOnnxVersion = "1.13.5"
val sherpaOnnxSha256 = "6419CD8BC983E0C4FAB06067F0FE0313FDC0F7103818AC1E7A08D50787B7A82B"
val sherpaOnnxUrl =
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/v$sherpaOnnxVersion/sherpa-onnx-$sherpaOnnxVersion.aar"
val sherpaOnnxAar = layout.buildDirectory.file("downloaded-dependencies/sherpa-onnx-$sherpaOnnxVersion.aar")
val generatedLegalAssets = layout.buildDirectory.dir("generated/legal-assets")

fun sha256(file: File): String {
    val digest = JavaMessageDigest.getInstance("SHA-256")
    file.inputStream().buffered().use { input ->
        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            digest.update(buffer, 0, count)
        }
    }
    return digest.digest().joinToString("") { "%02X".format(it) }
}

val prepareSherpaOnnxAar = tasks.register("prepareSherpaOnnxAar") {
    val targetProvider = sherpaOnnxAar
    val legacyCandidate = layout.projectDirectory.file("libs/sherpa-onnx-$sherpaOnnxVersion.aar")
    outputs.file(targetProvider)
    outputs.upToDateWhen {
        val target = targetProvider.get().asFile
        target.isFile && sha256(target) == sherpaOnnxSha256
    }

    doLast {
        val target = targetProvider.get().asFile
        target.parentFile.mkdirs()

        if (target.isFile) {
            val actual = sha256(target)
            if (actual == sherpaOnnxSha256) return@doLast
            target.delete()
            throw GradleException("Cached sherpa-onnx AAR SHA-256 mismatch: $actual")
        }

        val temporary = File(target.parentFile, "${target.name}.part")
        temporary.delete()
        try {
            val legacy = legacyCandidate.asFile
            if (legacy.isFile) {
                val actual = sha256(legacy)
                if (actual != sherpaOnnxSha256) {
                    throw GradleException("Local sherpa-onnx AAR SHA-256 mismatch: $actual")
                }
                legacy.copyTo(temporary, overwrite = true)
            } else {
                logger.lifecycle("Downloading verified sherpa-onnx $sherpaOnnxVersion runtime")
                val connection = JavaUri(sherpaOnnxUrl).toURL().openConnection().apply {
                    connectTimeout = 30_000
                    readTimeout = 120_000
                }
                connection.getInputStream().buffered().use { input ->
                    temporary.outputStream().buffered().use { output -> input.copyTo(output) }
                }
            }

            val actual = sha256(temporary)
            if (actual != sherpaOnnxSha256) {
                throw GradleException("Downloaded sherpa-onnx AAR SHA-256 mismatch: $actual")
            }
            try {
                JavaFiles.move(
                    temporary.toPath(),
                    target.toPath(),
                    JavaCopyOption.ATOMIC_MOVE,
                    JavaCopyOption.REPLACE_EXISTING
                )
            } catch (_: JavaAtomicMoveNotSupportedException) {
                JavaFiles.move(
                    temporary.toPath(),
                    target.toPath(),
                    JavaCopyOption.REPLACE_EXISTING
                )
            }
        } finally {
            temporary.delete()
        }
    }
}

val prepareLegalAssets = tasks.register<Copy>("prepareLegalAssets") {
    from(rootProject.projectDir.parentFile.resolve("THIRD_PARTY_NOTICES.md"))
    from(rootProject.projectDir.parentFile.resolve("LICENSE")) {
        rename { "APACHE-2.0-LICENSE.txt" }
    }
    into(generatedLegalAssets.map { it.dir("legal") })
}

val releaseStoreFile = System.getenv("NEXUS_RELEASE_STORE_FILE")
val releaseStorePassword = System.getenv("NEXUS_RELEASE_STORE_PASSWORD")
val releaseKeyAlias = System.getenv("NEXUS_RELEASE_KEY_ALIAS")
val releaseKeyPassword = System.getenv("NEXUS_RELEASE_KEY_PASSWORD")
val hasReleaseSigning = listOf(
    releaseStoreFile,
    releaseStorePassword,
    releaseKeyAlias,
    releaseKeyPassword
).all { !it.isNullOrBlank() }

android {
    namespace = "app.nexus.mobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "app.nexus.mobile"
        minSdk = 26
        targetSdk = 35
        versionCode = 33
        versionName = "0.1.17"

        ndk {
            abiFilters += "arm64-v8a"
        }

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("release") {
                storeFile = file(releaseStoreFile!!)
                storePassword = releaseStorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
            }
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            manifestPlaceholders["appLabel"] = "Nexus Debug"
        }
        release {
            manifestPlaceholders["appLabel"] = "Nexus"
            if (hasReleaseSigning) {
                signingConfig = signingConfigs.getByName("release")
            }
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.14"
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
        jniLibs.useLegacyPackaging = true
    }

    sourceSets.getByName("main").assets.srcDir(generatedLegalAssets)
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.core:core-ktx:1.19.0")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("io.coil-kt:coil-compose:2.7.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.google.code.gson:gson:2.14.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    implementation(files(sherpaOnnxAar))
    implementation("org.apache.commons:commons-compress:1.28.0")

    testImplementation("junit:junit:4.13.2")
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.11.0")

    androidTestImplementation(platform("androidx.compose:compose-bom:2024.12.01"))
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")

}

tasks.named("preBuild").configure {
    dependsOn(prepareSherpaOnnxAar, prepareLegalAssets)
}
