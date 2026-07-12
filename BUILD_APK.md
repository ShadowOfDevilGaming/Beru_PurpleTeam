# Building the Beru AI APK

`buildozer` does **not** run on native Windows. It needs Linux. Pick **one**
of the three paths below. All three produce the same file:
`bin/BeruAI-1.0.0-debug.apk`.

> First-time build downloads the Android SDK + NDK (~5-8 GB) and takes
> 30-60 minutes. Subsequent builds are a few minutes.

---

## Path A — WSL2 (Ubuntu) on this Windows machine  *(recommended locally)*

### 1. Enable WSL2 (one-time, needs admin + reboot)

Open **PowerShell as Administrator** and run:

```powershell
wsl --install -d Ubuntu
```

Reboot when prompted. On first launch it asks for a username + password.

### 2. Inside Ubuntu, install build dependencies

```bash
sudo apt update
sudo apt install -y \
    autoconf automake libtool pkg-config \
    zlib1g-dev libncurses5-dev libffi-dev libssl-dev \
    build-essential ccache git openjdk-17-jdk unzip
```

### 3. Reach your project files

WSL2 can read your Windows drive. From the Ubuntu shell:

```bash
cd "/mnt/c/Users/HP/OneDrive/Desktop/Beru_PurpleTeam"
```

> Tip: copy the project into `~/beru` inside WSL2 (`cp -r . ~/beru && cd ~/beru`)
> for much faster I/O than building straight off `/mnt/c`.

### 4. Create a venv and install buildozer

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip wheel Cython==0.29.36
pip install buildozer
```

### 5. Build the APK

```bash
buildozer -v android debug
```

When it finishes, the APK is at `bin/BeruAI-1.0.0-debug.apk`.

Copy it back to Windows:

```bash
cp bin/*.apk /mnt/c/Users/HP/OneDrive/Desktop/
```

---

## Path B — Docker (cleanest, no host pollution)

Requires **Docker Desktop** with the WSL2 backend installed.

```bash
cd "C:\Users\HP\OneDrive\Desktop\Beru_PurpleTeam"

# one-time, pulls a ~3 GB image
docker run --rm -it \
    -v "%cd%:/home/user/hostcwd" \
    kivy/buildozer android debug
```

Replace `%cd%` with `$PWD` if you're in Git Bash. The APK lands in
`./bin/`. If the official image is missing, build a minimal one:

```dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y \
    autoconf automake libtool pkg-config zlib1g-dev libncurses5-dev \
    libffi-dev libssl-dev build-essential ccache git openjdk-17-jdk \
    python3 python3-pip python3-venv unzip
RUN python3 -m pip install --upgrade pip wheel Cython==0.29.36 buildozer
WORKDIR /app
```

---

## Path C — GitHub Actions (cloud; nothing installed locally)

The workflow file **is already included** in this package at
`.github/workflows/build-apk.yml`. Just push the repo to GitHub — the build
runs automatically.

**Quick start:**

1. Create an empty repo on GitHub (e.g. `beru-ai`), public or private.
2. From this folder:
   ```bash
   git init
   git add .
   git commit -m "Beru AI build-ready"
   git branch -M main
   git remote add origin https://github.com/<you>/beru-ai.git
   git push -u origin main
   ```
3. Open the repo → **Actions** tab → watch **"Build Android APK"** run.
   - First run: ~25-40 min (downloads the Android SDK/NDK).
   - Later runs: a few minutes (SDK/NDK cached by the workflow).
4. When it finishes, click the run → scroll to **Artifacts** → download
   **`BeruAI-apk`** (a zip containing `BeruAI-1.0.0-debug.apk`).

**What the workflow does** (14 steps):
- Installs build deps + JDK 17, pins `Cython==0.29.36` (Kivy 2.3 breaks on Cython 3).
- Caches `~/.buildozer`, the project `.buildozer/`, and pip — keyed on the
  spec hash, so a requirements change busts the cache safely.
- Runs `buildozer -v android debug`.
- Uploads the APK as the `BeruAI-apk` artifact (30-day retention).
- On a `v*` tag push (`git tag v1.0.0 && git push --tags`), attaches the APK
  to a GitHub Release automatically.
- On failure, dumps + uploads buildozer logs as `BeruAI-build-logs`.

**Manual trigger:** Actions tab → "Build Android APK" → **Run workflow**.

The workflow only rebuilds when source files change (`main.py`,
`openrouter_client.py`, `buildozer.spec`, `shadow_memory.json`, or the
workflow itself) — edits to docs won't burn a build.

> The full file lives at `.github/workflows/build-apk.yml` — open it to tune
> triggers, retention, or model/permissions.

---

## Installing the APK on a phone

**Via adb (USB debugging on):**

```bash
adb install bin/BeruAI-1.0.0-debug.apk
```

**Manually:** copy the APK to the phone, tap it in a file manager, and allow
"Install from unknown sources" when prompted.

On first launch, Android will prompt for:
- **Display over other apps** (SYSTEM_ALERT_WINDOW) — for the overlay service.
- **Microphone** (RECORD_AUDIO) — optional, for the audio pipeline.
- **Foreground service** notification — shown while the overlay runs.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Java JDK 17 ... not found` | `sudo apt install openjdk-17-jdk`, or set `JAVA_HOME`. |
| Build fails on `Cython` compile error | Pin `Cython==0.29.36` (Kivy 2.3 is incompatible with Cython 3). |
| `SDK license not accepted` | Set `android.accept_sdk_license = True` in buildozer.spec (already set). |
| Out of space | First build needs ~10 GB free in the build dir; clean with `buildozer android clean`. |
| Slow over `/mnt/c` | Move project into the WSL2 home dir (`~/beru`) before building. |
| `python-for-android` recipe error | Try `p4a.branch = stable` in buildozer.spec instead of `master`. |
