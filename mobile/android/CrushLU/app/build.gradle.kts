plugins {
    id("com.android.application")
}

val uploadStoreFile = providers.gradleProperty("CRUSH_UPLOAD_STORE_FILE")

android {
    namespace = "lu.crush.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "lu.crush.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
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
