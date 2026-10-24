plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "dev.tablink"
    compileSdk = 37

    defaultConfig {
        applicationId = "dev.tablink"
        minSdk = 30
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
    }
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.material3)

    // The RDP engine: FreeRDP's freeRDPCore, built from source by scripts/build-freerdp.sh,
    // plus the libraries it is built against.
    implementation(files("libs/freeRDPCore-release.aar"))
    // Built-in Tailscale node (tsnet), built by scripts/build-tsnet.sh.
    implementation(files("libs/tablinknet.aar"))
    implementation(libs.freerdp.appcompat)
    implementation(libs.freerdp.core)
    implementation(libs.freerdp.preference)
    implementation(libs.freerdp.recyclerview)
    implementation(libs.freerdp.lifecycle.viewmodel)
    implementation(libs.freerdp.lifecycle.livedata)
    implementation(libs.freerdp.room.runtime)
    implementation(libs.freerdp.sqlcipher) { artifact { type = "aar" } }
    implementation(libs.freerdp.sqlite)

    testImplementation(libs.junit)
}
