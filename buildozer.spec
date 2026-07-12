[app]

# App metadata -----------------------------------------------------------------
title = Beru AI
package.name = beruai
package.domain = ai.beru

# Source code & entry point ----------------------------------------------------
source.dir = .
source.exts = py,png,jpg,kv,atlas,json

version = 1.0.0

# Requirements: Kivy UI stack + pure-stdlib client (no extra pip deps needed).
# urllib/threading/json are stdlib, so only the Kivy stack is listed.
requirements = python3,kivy==2.3.0

# Orientation / fullscreen on Android ------------------------------------------
orientation = portrait
fullscreen = 0

# Android runtime tweaks --------------------------------------------------------
# Use python3crystax-free threading-friendly build where available; fall back to
# the standard recipe otherwise.
android.archs = arm64-v8a, armeabi-v7a

# Permissions -------------------------------------------------------------------
# INTERNET            -> OpenRouter HTTP traffic
# RECORD_AUDIO        -> voice input pipeline (planned)
# SYSTEM_ALERT_WINDOW -> floating overlay service
# FOREGROUND_SERVICE  -> keeps the overlay + audio loop alive in the background
# WAKE_LOCK           -> low-battery background polling stays scheduled
android.permissions = INTERNET,RECORD_AUDIO,SYSTEM_ALERT_WINDOW,FOREGROUND_SERVICE,WAKE_LOCK

# Foreground service / notification channel metadata (Android 8+ requirement).
android.api = 34
android.minapi = 24
android.accept_sdk_license = True

# Tell buildozer to build a foreground-service-enabled bootstrap so the overlay
# service can survive when Beru AI is backgrounded.
services = BeruOverlay:main.py:run_overlay_service

# Bubble / overlay (Android) support metadata used by the foreground service.
android.allow_backup = False
# Keep the CPU awake while the foreground service is running (WAKE_LOCK).
android.wakelock = True

# Assets bundled into the APK --------------------------------------------------
include = shadow_memory.json, assets

# Build configuration ----------------------------------------------------------
# bdist/generic build type. Adjust for the CI environment if needed.
# p4a branch pinned for reproducible builds.
p4a.branch = master
# CPython 3 build (python-for-android >= 2024 uses recipe python3).
ios.kivy_ios_url = https://github.com/kivy/kivy-ios
ios.kivy_ios_branch = master

# Icon / presplash (drop PNGs in ./assets to enable) ---------------------------
icon.filename = %(source.dir)s/assets/icon.png
presplash.filename = %(source.dir)s/assets/presplash.png
presplash.color = #120609

# Logging / build verbosity ----------------------------------------------------
log_level = 2
warn_on_root = 1

# Build artefacts --------------------------------------------------------------
build_dir = ./.buildozer
bin_dir = ./bin


[buildozer]

# Target platform --------------------------------------------------------------
log_level = 2
warn_on_root = 1
