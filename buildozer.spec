[app]

# App metadata -----------------------------------------------------------------
title = Beru AI
package.name = beruai
package.domain = ai.beru

# Source code & entry point ----------------------------------------------------
source.dir = .
source.exts = py,kv,atlas,json

version = 1.0.0

# Requirements: Pinned Kivy stack to ensure dynamic toolchain stability
requirements = python3,kivy==2.3.0

# Orientation / fullscreen on Android ------------------------------------------
orientation = portrait
fullscreen = 0

# Android runtime tweaks --------------------------------------------------------
android.archs = arm64-v8a, armeabi-v7a

# Permissions -------------------------------------------------------------------
android.permissions = INTERNET,RECORD_AUDIO,SYSTEM_ALERT_WINDOW,FOREGROUND_SERVICE,WAKE_LOCK

# Foreground service / notification channel metadata
android.api = 34
android.minapi = 24
android.accept_sdk_license = True

# Foreground Service setup for Beru AI Overlay
services = BeruOverlay:main.py:run_overlay_service

android.allow_backup = False
android.wakelock = True

# Assets bundled into the APK --------------------------------------------------
include = shadow_memory.json

# Build configuration & Engine Locks -------------------------------------------
p4a.branch = master

# --- FORCE SYSTEM PRE-INSTALLED NDK TARGET BY SHADOW MASTER ---
# This bypasses the NDK download/unzip extraction error 9 completely
android.ndk = 27.3.13750724
android.sdk_path = /usr/local/lib/android/sdk
android.ndk_path = /usr/local/lib/android/sdk/ndk/27.3.13750724

# Logging / build verbosity ----------------------------------------------------
log_level = 2
warn_on_root = 1

# Build artefacts --------------------------------------------------------------
build_dir = ./.buildozer
bin_dir = ./bin


[buildozer]
log_level = 2
warn_on_root = 1