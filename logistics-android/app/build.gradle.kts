plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}

/**
 * API 根地址，构建期注入，源码与仓库不落真实端点。
 * 优先级：-PapiBaseUrl > gradle.properties(apiBaseUrl) > 环境变量 API_BASE_URL > 占位值
 */
val apiBaseUrl: String = (project.findProperty("apiBaseUrl") as String?)
    ?: System.getenv("API_BASE_URL")
    ?: "https://api.example.invalid"

android {
    namespace = "com.company.logistics"
    compileSdk = 34
    defaultConfig {
        applicationId = "com.company.logistics"
        minSdk = 26
        targetSdk = 34
        versionCode = 3
        versionName = "0.3.0"

        buildConfigField("String", "API_BASE_URL", "\"$apiBaseUrl\"")
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
}
