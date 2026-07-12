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
# IMPORTANT: pin to RELEASED Kivy (not git master). p4a master was silently
# pulling NDK r28c (its extraction fails with "error code 9 / broken pipe")
# and building an unstable Python 3.14. Stable Kivy + Cython 0.29.x is the
# combination python-for-android supports and tests.
requirements = python3,kivy==2.3.0

# Orientation / fullscreen on Android ------------------------------------------
orientation = portrait
fullscreen = 0

# Android runtime --------------------------------------------------------------
android.archs = arm64-v8a, armeabi-v7a
android.api = 34
android.minapi = 24
android.accept_sdk_license = True

# Permissions ------------------------------------------------------------------
# INTERNET            -> OpenRouter HTTP traffic
# RECORD_AUDIO        -> voice input pipeline
# SYSTEM_ALERT_WINDOW -> floating overlay service
# FOREGROUND_SERVICE  -> keeps the overlay + audio loop alive in the background
# WAKE_LOCK           -> low-battery background polling stays scheduled
android.permissions = INTERNET,RECORD_AUDIO,SYSTEM_ALERT_WINDOW,FOREGROUND_SERVICE,WAKE_LOCK

# Foreground service: the entry point run_overlay_service() is defined in main.py.
services = BeruOverlay:main.py:run_overlay_service

android.allow_backup = False
android.wakelock = True

# Assets bundled into the APK --------------------------------------------------
include = shadow_memory.json

# Build engine ---------------------------------------------------------------
# p4a.branch must be a REAL tag:
#   * empty -> buildozer runs `git clone -b ''` -> git error 128 (the previous
#     failure we just hit).
#   * master -> auto-downloads NDK r28c, whose zip extraction fails on the
#     runner with "error code 9 / broken pipe".
# Pin to the latest released tag instead. This p4a respects the NDK pinned
# below and builds the stable Python 3 recipe.
p4a.branch = v2026.05.09

# --- Use the GitHub runner's PREINSTALLED NDK (no download) -------------------
# The ubuntu runner ships NDK 27.3.13750724. Setting android.ndk + the full
# _path/_version pins tells p4a to reuse it instead of fetching r28c.
android.ndk = 27.3.13750724
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
