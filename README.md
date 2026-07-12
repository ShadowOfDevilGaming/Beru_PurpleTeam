# Beru AI

A dark-themed (deep crimson on near-black) mobile AI companion built with
**Kivy + python-for-android**. Talks to OpenRouter (default model
`cognitivecomputations/dolphin-mixtral-8x7b`), keeps a local "Shadow Memory"
vault, runs a purple-team security configuration auditor, and supports a
low-power background overlay + audio service.

> API key is entered at runtime in-app (API Switcher screen). No key is baked
> into the build.

---

## Project layout

```
Beru_PurpleTeam/
├── main.py                # Kivy UI + Shadow Memory + Purple Team Auditor + Overlay service
├── openrouter_client.py   # Thread-safe OpenRouter HTTP client (runtime key swap)
├── buildozer.spec         # Android packaging config (permissions, name, service)
├── shadow_memory.json     # Local vault (seeded schema; rewritten at runtime)
├── requirements.txt       # Desktop dev dependencies (Android uses buildozer.spec)
├── README.md              # This file
├── BUILD_APK.md           # Step-by-step instructions to produce the .apk
├── .github/workflows/     # GitHub Actions: push to GitHub -> get the APK as an artifact
│   └── build-apk.yml      #   (Path C in BUILD_APK.md)
└── assets/                # Drop icon.png / presplash.png here before building
```

## Modules (per build spec)

| # | Module | Where |
|---|--------|-------|
| 1 | Dynamic API Switcher | `ApiSwitcherScreen` + `OpenRouterClient.set_api_key()` |
| 2 | Shadow Memory Vault  | `ShadowMemoryVault` (local JSON, atomic writes) |
| 3 | System Overlay & Audio pipeline | `OverlayService` + `AudioPipeline` + `run_overlay_service()` |
| 4 | Purple Team Auditing Engine | `PurpleTeamAuditor` (diagnostic only; no exploit payloads) |

## Run on desktop (for development)

```bash
pip install -r requirements.txt
python main.py
```

A 390x844 phone-shaped window opens. Enter your OpenRouter key in the **API**
screen and click Verify.

## Build the Android APK

**You cannot build the APK natively on Windows.** buildozer needs Linux.
See **`BUILD_APK.md`** for the three supported paths (WSL2, Docker, or
GitHub Actions) with copy-paste commands. **Easiest: GitHub Actions** — the
workflow is already in `.github/workflows/build-apk.yml`; just `git push`
to GitHub and download the APK from the Actions artifacts.

The short version (on any Linux machine / WSL2 Ubuntu):

```bash
sudo apt update && sudo apt install -y autoconf automake libtool \
    pkg-config zlib1g-dev libncurses5-dev libffi-dev libssl-dev \
    build-essential ccache git openjdk-17-jdk
python3 -m venv venv && source venv/bin/activate
pip install --upgrade pip wheel Cython buildozer
buildozer -v android debug
```

The signed APK appears at `bin/BeruAI-1.0.0-debug.apk` after ~30-60 min on the
first run (it downloads the Android SDK/NDK, ~5-8 GB).

## Install on a phone

```bash
adb install bin/BeruAI-1.0.0-debug.apk
```

Or copy the APK to the phone and tap it (enable "Install from unknown sources").
On first launch grant: overlay (SYSTEM_ALERT_WINDOW), microphone (optional),
and the foreground-service notification.

## Security note

The Purple Team Auditing Engine is **strictly diagnostic**. It parses a
configuration layout, matches it against curated risk patterns, and emits
*architectural* remediation guidance. It contains no executable exploit
payloads and performs no live network disruption.
