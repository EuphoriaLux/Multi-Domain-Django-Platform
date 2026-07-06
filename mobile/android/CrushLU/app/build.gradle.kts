plugins {
    id("com.android.application")
}

val uploadStoreFile = providers.gradleProperty("CRUSH_UPLOAD_STORE_FILE")
val env = providers.gradleProperty("CRUSH_ENV").getOrElse("production")
val baseUrl = if (env == "staging") "https://test.crush.lu" else "https://crush.lu"

android {
    namespace = "lu.crush.app"
    compileSdk = 35

    buildFeatures {
        buildConfig = true
    }

    defaultConfig {
        applicationId = "lu.crush.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
        buildConfigField("String", "BASE_URL", "\"$baseUrl\"")
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
}
