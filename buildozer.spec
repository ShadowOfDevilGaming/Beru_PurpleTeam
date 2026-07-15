[app]

# App metadata -----------------------------------------------------------------
title = Beru AI
package.name = beruai
package.domain = ai.beru

# Source code & entry point ----------------------------------------------------
source.dir = .
source.exts = py,kv,atlas,json

version = 1.0.0

# Requirements ----------------------------------------------------------------
# Pinned to master recipes for modern pip requirements compilation trace
requirements = python3, kivy, openrouter_client

# Orientation / fullscreen on Android ------------------------------------------
orientation = portrait
fullscreen = 0

# Android runtime --------------------------------------------------------------
android.archs = arm64-v8a
android.api = 34
android.minapi = 24
android.accept_sdk_license = True

# Permissions ------------------------------------------------------------------
android.permissions = INTERNET,RECORD_AUDIO,SYSTEM_ALERT_WINDOW,FOREGROUND_SERVICE,WAKE_LOCK

# Foreground service: entry point run_overlay_service() defined in main.py
services = BeruOverlay:main.py:run_overlay_service

android.allow_backup = False
android.wakelock = True

# Assets bundled into the APK --------------------------------------------------
include = shadow_memory.json

# Build engine ---------------------------------------------------------------
# Patched: Shifted to master to allow pythonforandroid to bundle modern pip requirements seamlessly
p4a.branch = master

# --- FIXED SYSTEM PRE-INSTALLED NDK TARGET LINKS BY SHADOW MASTER ---
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