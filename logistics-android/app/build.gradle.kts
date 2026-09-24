plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}

/**
 * API 根地址，构建期注入，源码与仓库不落真实端点。
 * 优先级：-PapiBaseUrl=...（Gradle 属性键 apiBaseUrl） > gradle.properties(apiBaseUrl)
 * > 环境变量 API_BASE_URL > 占位值
 */
val apiBaseUrl: String = sequenceOf(
    project.findProperty("apiBaseUrl") as String?,
    System.getenv("API_BASE_URL"),
).filterNotNull().firstOrNull { it.isNotBlank() }
    ?: "https://api.example.invalid"

// BuildConfig is generated Kotlin/Java source, so escape build-time input before embedding it.
val escapedApiBaseUrl = apiBaseUrl
    .replace("\\", "\\\\")
    .replace("\"", "\\\"")
    .replace("\r", "\\r")
    .replace("\n", "\\n")

android {
    namespace = "com.company.logistics"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.company.logistics"
        minSdk = 26
        targetSdk = 34
        versionCode = 25
        versionName = "0.5.11"

        buildConfigField("String", "API_BASE_URL", "\"$escapedApiBaseUrl\"")
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    buildTypes {
        debug {
            isDebuggable = true
        }
        release {
            isDebuggable = false
            // Release remains buildable for packaging checks; production shrinking/signing is CI-owned.
            isMinifyEnabled = false
            isShrinkResources = false
        }
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.09.03"))
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.6")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")
    implementation("androidx.work:work-runtime-ktx:2.9.1")

    // Filament 1.71.5 is pinned and resolved from Maven Central (Google Maven stopped at 1.52.0).
    // 1.71.5 ships 16 KB-aligned arm64 shared objects; 1.52.0 was 4 KB-aligned and failed to load
    // on Android 15+ 16 KB-page devices, surfacing as FILAMENT_UNAVAILABLE in the debug panel.
    implementation("com.google.android.filament:filament-android:1.71.5")
    implementation("com.google.android.filament:gltfio-android:1.71.5")

    // ---- 摄像头扫码（版本锁定，不使用动态版本号）----
    // CameraX 1.3.4：兼容 compileSdk 34 / Compose BOM 2024.09.03
    implementation("androidx.camera:camera-core:1.3.4")
    implementation("androidx.camera:camera-camera2:1.3.4")
    implementation("androidx.camera:camera-lifecycle:1.3.4")
    implementation("androidx.camera:camera-view:1.3.4")
    // ProcessCameraProvider.getInstance(...).await() 需要
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-guava:1.8.1")
    // ML Kit 条码识别：本地离线识别，不依赖网络
    implementation("com.google.mlkit:barcode-scanning:17.3.0")

    // 会话加密存储：refresh token 长效（30 天），必须落盘且加密。
    // 使用 Android Keystore 托管密钥的 AES-256-GCM。
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    testImplementation("junit:junit:4.13.2")
    // Android 本地单测不提供 org.json 实现（android.jar 仅含桩）；用同包 JVM 实现验证 parser。
    testImplementation("org.json:json:20240303")

    androidTestImplementation(platform("androidx.compose:compose-bom:2024.09.03"))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.room:room-testing:2.6.1")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}
