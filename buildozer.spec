[app]

# App metadata -----------------------------------------------------------------
title = Beru AI
package.name = beruai
package.domain = ai.beru

# Source code & entry point ----------------------------------------------------
source.dir = .
source.include_exts = py,kv,atlas,json

version = 1.0.1

# Requirements ----------------------------------------------------------------
# Kivy 2.3.0 sdist: iske pre-generated C files Python 3.11 ke liye bane hain.
# p4a v2024.01.21 bhi Python 3.11 build karta hai -> PERFECT MATCH.
#   p4a master/develop -> Python 3.13/3.14 -> Kivy C files compile fail karte
#   hain (_PyInterpreterState_GetConfig / _PyList_Extend undefined). 6 baar fail.
requirements = python3, kivy==2.3.0

# Orientation / fullscreen on Android ------------------------------------------
orientation = portrait
fullscreen = 0

# Android runtime --------------------------------------------------------------
# arm64-v8a only: sab modern phones arm64 hain, build time aadha.
android.archs = arm64-v8a
android.api = 34
android.minapi = 24
android.accept_sdk_license = True

# Permissions ------------------------------------------------------------------
# INTERNET            -> OpenRouter HTTP traffic
# RECORD_AUDIO        -> voice input / wake word
# SYSTEM_ALERT_WINDOW -> floating overlay service
# FOREGROUND_SERVICE  -> keeps overlay + audio loop alive in background
# WAKE_LOCK           -> background polling stays scheduled
android.permissions = INTERNET,RECORD_AUDIO,SYSTEM_ALERT_WINDOW,FOREGROUND_SERVICE,WAKE_LOCK

# Foreground service: entry point run_overlay_service() defined in main.py
services = BeruOverlay:main.py:run_overlay_service

android.allow_backup = False
android.wakelock = True

# Assets bundled into the APK --------------------------------------------------
include = shadow_memory.json

# Build engine ---------------------------------------------------------------
# *** THE CRITICAL FIX ***
# p4a.branch = v2024.01.21 -> Python 3.11 build karta hai (stable).
# p4a master Python 3.13/3.14 build karta hai, jiske saath Kivy 2.3.0 ke
# pre-generated C files incompatible hain (compile fail).
p4a.branch = v2024.01.21

# NDK: do NOT pin here. Workflow pre-downloads + validates r28c (proven working
# since build #10). p4a v2024.01.21 is NDK r28c ke saath compatible hai.

# Logging / build verbosity ----------------------------------------------------
log_level = 2
warn_on_root = 1

# Build artefacts --------------------------------------------------------------
build_dir = ./.buildozer
bin_dir = ./bin


[buildozer]
log_level = 2
warn_on_root = 1
