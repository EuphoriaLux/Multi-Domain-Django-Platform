plugins {
    id("com.android.application")
    id("com.google.gms.google-services")
}

val uploadStoreFile = providers.gradleProperty("CRUSH_UPLOAD_STORE_FILE")
val env = providers.gradleProperty("CRUSH_ENV").getOrElse("production")
val baseUrl = if (env == "staging") "https://test.crush.lu" else "https://crush.lu"

val isStaging = (env == "staging")
val appName = if (isStaging) "Crush (Staging)" else "Crush.lu"
val authScheme = if (isStaging) "crushlustaging" else "crushlu"
val hostName = if (isStaging) "test.crush.lu" else "crush.lu"

android {
    namespace = "lu.crush.app"
    compileSdk = 35

    buildFeatures {
        buildConfig = true
    }

    defaultConfig {
        applicationId = "lu.crush.app"
        if (isStaging) {
            applicationIdSuffix = ".staging"
        }
        minSdk = 26
        targetSdk = 35
        versionCode = 3
        versionName = "1.0.2"
        buildConfigField("String", "BASE_URL", "\"$baseUrl\"")
        buildConfigField("String", "AUTH_SCHEME", "\"$authScheme\"")

        manifestPlaceholders["appName"] = appName
        manifestPlaceholders["appHost"] = hostName
        manifestPlaceholders["authScheme"] = authScheme
    }

    signingConfigs {
        create("release") {
            if (uploadStoreFile.isPresent) {
                storeFile = file(uploadStoreFile.get())
                storePassword = providers.gradleProperty("CRUSH_UPLOAD_STORE_PASSWORD").orNull
                keyAlias = providers.gradleProperty("CRUSH_UPLOAD_KEY_ALIAS").orNull
                keyPassword = providers.gradleProperty("CRUSH_UPLOAD_KEY_PASSWORD").orNull
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (uploadStoreFile.isPresent) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("androidx.constraintlayout:constraintlayout:2.2.0")
    implementation("androidx.core:core-splashscreen:1.0.1")
    implementation("com.google.android.material:material:1.12.0")

    // Firebase Cloud Messaging (FCM)
    implementation(platform("com.google.firebase:firebase-bom:33.1.2"))
    implementation("com.google.firebase:firebase-messaging")
}
