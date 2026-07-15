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
# Kivy from git source (tag 2.3.0): p4a develop Python 3.13 build karta hai.
# Kivy 2.3.0 sdist ke pre-generated C files Py3.13 ke saath incompatible hain
# (_PyList_Extend etc). Git source se Cython 3.0 C files regenerate karta hai
# target Python ke liye. Yeh combination p4a develop ke saath tested hai.
requirements = python3, git+https://github.com/kivy/kivy.git@2.3.0

# Orientation / fullscreen on Android ------------------------------------------
orientation = portrait
fullscreen = 0

# Android runtime --------------------------------------------------------------
# Single arch arm64-v8a only: 2017+ ke saare phones arm64 hain. armv7 build
# tha jaga crash hone ka sabse bada reason (p4a 32-bit Python compile fail).
# Ek arch = aadha build time, zyada reliable.
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
# p4a.branch = master (the only well-tested branch for Kivy 2.3.0).
# Workflow pre-downloads and validates the NDK before buildozer runs.
p4a.branch = master

# Logging / build verbosity ----------------------------------------------------
log_level = 2
warn_on_root = 1

# Build artefacts --------------------------------------------------------------
build_dir = ./.buildozer
bin_dir = ./bin


[buildozer]
log_level = 2
warn_on_root = 1
