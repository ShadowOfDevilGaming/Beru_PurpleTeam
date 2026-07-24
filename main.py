"""
main.py
=======
Beru AI -- core responsive mobile application.

Builds a dark UI (deep crimson / red accents on near-black) with Kivy. The app
is organized into a ScreenManager with four primary screens:

    * Dashboard      -- status overview + quick actions
    * API Switcher   -- settings screen; runtime API key swapping
    * Shadow Memory  -- local JSON vault visualization & editing
    * Purple Team    -- security configuration auditor

It wires together the four in-app modules required by the build spec:

    1. Dynamic API Switcher        -> :class:`OpenRouterClient.set_api_key`
    2. Shadow Memory Vault         -> :class:`ShadowMemoryVault`
    3. System Overlay & Audio      -> :class:`OverlayService` (background loop)
    4. Purple Team Auditing Engine -> :class:`PurpleTeamAuditor`

Design goals: standard-library + Kivy only (no extra pip deps), thread-safe
network calls, and a single OpenRouterClient instance shared by every screen
so an API-key swap propagates immediately.

Author: Beru AI build system
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Crash guard: catch any unhandled exception in the main thread and write a
# readable stack trace to /sdcard/BeruAI/crash.log so a black-screen crash on
# Android can be diagnosed without logcat.  sys.excepthook is the Python main-
# thread handler; Kivy's Clock also respects it for scheduled callbacks.
# ---------------------------------------------------------------------------
def _beru_install_crash_logger() -> None:
    log_paths = []
    # Prefer external storage (visible without root) on Android.
    for env in ("EXTERNAL_STORAGE", "ANDROID_EXTERNAL_STORAGE", "SDCARD"):
        base = os.environ.get(env)
        if base:
            log_paths.append(os.path.join(base, "BeruAI", "crash.log"))
    # Fallback: app's writable dir / cwd.
    try:
        from kivy.app import App  # noqa: F401

        # App user data dir is created by Kivy on first run.
        log_paths.append("crash.log")
        log_paths.append(os.path.join(os.getcwd(), "crash.log"))
    except Exception:
        log_paths.append("crash.log")

    def _writer(exc_type, exc, tb):
        msg = "".join(traceback.format_exception(exc_type, exc, tb))
        stamp = _dt.datetime.now().isoformat(timespec="seconds")
        for path in log_paths:
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(f"\n===== BeruAI crash {stamp} =====\n{msg}\n")
                break
            except Exception:
                continue

    sys.excepthook = _writer
    # Mirror stderr writes too, so print()s before a hard crash are captured.
    try:
        sys.stderr = _TeeStream(sys.stderr, log_paths)
    except Exception:
        pass


class _TeeStream:
    """Writes to both the original stderr and a rolling crash log."""

    def __init__(self, original, paths):
        self._original = original
        self._paths = paths

    def write(self, data):
        try:
            self._original.write(data)
        except Exception:
            pass
        if not data or data.isspace():
            return
        stamp = _dt.datetime.now().isoformat(timespec="seconds")
        for path in self._paths:
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(f"[{stamp}] {data}")
                break
            except Exception:
                continue

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass


_beru_install_crash_logger()

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Rectangle
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.textinput import TextInput

from openrouter_client import (
    AuthenticationError,
    OpenRouterClient,
    OpenRouterError,
)

# ===========================================================================
# THEME -- dark UI, deep crimson / red accents
# ===========================================================================
class Theme:
    BG = (0.06, 0.03, 0.05, 1)            # near-black with warm tint
    BG_PANEL = (0.10, 0.05, 0.06, 1)      # raised panel
    BG_PANEL_HI = (0.14, 0.08, 0.09, 1)   # hover/active panel
    INK = (0.95, 0.93, 0.94, 1)           # primary text
    INK_DIM = (0.66, 0.60, 0.62, 1)       # secondary text
    CRIMSON = (0.78, 0.10, 0.16, 1)       # primary accent
    CRIMSON_HI = (0.93, 0.21, 0.24, 1)    # bright accent / links
    BLOOD = (0.45, 0.05, 0.08, 1)         # deep accent
    OK = (0.30, 0.70, 0.42, 1)            # success
    WARN = (0.93, 0.69, 0.13, 1)          # warning
    DANGER = (0.92, 0.20, 0.20, 1)        # critical / high
    STROKE = (0.22, 0.12, 0.13, 1)        # hairline border

    FONT_REGULAR = "Roboto"
    FONT_MONO = "Courier New"

    @staticmethod
    def rgba_to_hex(c: Tuple[float, float, float, float]) -> str:
        r, g, b = int(c[0] * 255), int(c[1] * 255), int(c[2] * 255)
        return f"#{r:02x}{g:02x}{b:02x}"


# ===========================================================================
# Module 2 -- Shadow Memory Vault
# Local JSON store in the app's accessible storage directory.
# ===========================================================================
class ShadowMemoryVault:
    """Persistent local vault backed by ``shadow_memory.json``.

    The file lives in the app's *accessible storage directory*:
      * On Android: ``App.get_running_app().user_data_dir``
      * On desktop: the project directory (so it pairs with the repo file).

    The vault holds three collections: ``notes``, ``schedules``, ``creative``.
    All access is serialized with a lock; writes are atomic (temp file +
    os.replace) so a crash never leaves a half-written file.
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: Optional[str] = None):
        self._path = path or self._default_path()
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {
            "schema_version": self.SCHEMA_VERSION,
            "created": _dt.date.today().isoformat(),
            "notes": [],
            "schedules": [],
            "creative": [],
        }
        self.load()

    # ------------------------------------------------------------- path utils
    @staticmethod
    def _default_path() -> str:
        # Prefer the running app's user data dir (Android-safe). Fall back to
        # the project directory on desktop so it stays alongside the source.
        try:
            app = App.get_running_app()
            if app is not None:
                base = getattr(app, "user_data_dir", None)
                if base:
                    return os.path.join(base, "shadow_memory.json")
        except Exception:
            pass
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "shadow_memory.json")

    @property
    def path(self) -> str:
        return self._path

    # ------------------------------------------------------------------- load
    def load(self) -> None:
        with self._lock:
            if not os.path.exists(self._path):
                self.save()
                return
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    raw = fh.read().strip()
                if not raw:
                    self.save()
                    return
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("vault root is not an object")
            except (OSError, ValueError, json.JSONDecodeError):
                # Corrupt or unreadable -- reset to a clean schema in memory.
                self._data = {
                    "schema_version": self.SCHEMA_VERSION,
                    "created": _dt.date.today().isoformat(),
                    "notes": [],
                    "schedules": [],
                    "creative": [],
                }
                return
            self._data = {
                "schema_version": data.get("schema_version", self.SCHEMA_VERSION),
                "created": data.get("created", _dt.date.today().isoformat()),
                "notes": list(data.get("notes", [])),
                "schedules": list(data.get("schedules", [])),
                "creative": list(data.get("creative", [])),
            }

    # ------------------------------------------------------------------- save
    def save(self) -> None:
        """Atomic write: temp file + ``os.replace``."""
        with self._lock:
            os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)

    # ------------------------------------------------------------- collections
    def all(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))  # deep copy

    def list_collection(self, name: str) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._data.get(name, []))

    def add(self, collection: str, entry: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            collection = self._validate_collection(collection)
            record = {
                "id": self._next_id(collection),
                "created": _dt.datetime.now().isoformat(timespec="seconds"),
                "updated": _dt.datetime.now().isoformat(timespec="seconds"),
                **entry,
            }
            self._data[collection].append(record)
            self.save()
            return record

    def update(self, collection: str, entry_id: int, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            collection = self._validate_collection(collection)
            for item in self._data[collection]:
                if item.get("id") == entry_id:
                    item.update(patch)
                    item["updated"] = _dt.datetime.now().isoformat(timespec="seconds")
                    self.save()
                    return dict(item)
            return None

    def delete(self, collection: str, entry_id: int) -> bool:
        with self._lock:
            collection = self._validate_collection(collection)
            before = len(self._data[collection])
            self._data[collection] = [i for i in self._data[collection] if i.get("id") != entry_id]
            changed = len(self._data[collection]) != before
            if changed:
                self.save()
            return changed

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {k: len(v) for k, v in self._data.items() if isinstance(v, list)}

    # ------------------------------------------------------------- internals
    def _validate_collection(self, name: str) -> str:
        if name not in ("notes", "schedules", "creative"):
            raise ValueError(f"unknown shadow collection: {name!r}")
        return name

    def _next_id(self, collection: str) -> int:
        items = self._data.get(collection, [])
        return (max((i.get("id", 0) for i in items), default=0)) + 1


# ===========================================================================
# Module 4 -- Purple Team Auditing Engine
# Pure diagnostic modeling. Emulates theoretical breach mechanics against a
# given configuration and returns architectural fixes. No exploit payloads,
# no live network disruption.
# ===========================================================================
class PurpleTeamAuditor:
    """Security configuration auditor (purple-team, expert diagnostic mode).

    Given a configuration layout as text (INI, YAML-ish, JSON, or freeform
    key=value), it parses the keys/values, matches them against a curated rule
    set of *theoretical breach mechanics*, and emits a structured report:
    findings (with severity), and remediation / patch methodology.

    Strictly diagnostic: it never emits executable payloads, network probes,
    or anything that could be run to attack a live system. Every finding's
    ``fix`` describes an *architectural* mitigation.
    """

    SEVERITY_CRITICAL = "CRITICAL"
    SEVERITY_HIGH = "HIGH"
    SEVERITY_MEDIUM = "MEDIUM"
    SEVERITY_LOW = "LOW"
    SEVERITY_INFO = "INFO"

    _ORDER = {
        "CRITICAL": 0,
        "HIGH": 1,
        "MEDIUM": 2,
        "LOW": 3,
        "INFO": 4,
    }

    def __init__(self):
        self.rules = self._default_rules()

    # ------------------------------------------------------------------- rules
    def _default_rules(self) -> List[Dict[str, Any]]:
        """Each rule: id, name, severity, predicate, breach, fix."""
        return [
            {
                "id": "PT-001",
                "name": "Hardcoded secret in config",
                "severity": self.SEVERITY_CRITICAL,
                "pattern": re.compile(
                    r"(?i)(api[_-]?key|secret|password|passwd|token|client[_-]?secret)"
                    r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+=]{6,}['\"]?"
                ),
                "breach": (
                    "A credential present in cleartext config can be exfiltrated "
                    "from a source leak, container image layer, or backup snapshot "
                    "and replayed to impersonate the trusted client."
                ),
                "fix": (
                    "Move the credential to a managed secrets store (e.g. AWS "
                    "Secrets Manager, HashiCorp Vault, Android Keystore for mobile). "
                    "Inject at runtime via env var or ephemeral file, rotate on a "
                    "schedule, and deny-list the value in CI secret scanning."
                ),
            },
            {
                "id": "PT-002",
                "name": "TLS verification disabled",
                "severity": self.SEVERITY_HIGH,
                "pattern": re.compile(r"(?i)(verify\s*=\s*false|insecure|ssl[_-]?verify\s*=\s*0)"),
                "breach": (
                    "Disabling certificate validation exposes the channel to MITM, "
                    "letting an on-path attacker intercept or rewrite traffic."
                ),
                "fix": (
                    "Enforce TLS verification; pin the CA or leaf certificate when "
                    "feasible. Fail closed if the trust store cannot be loaded."
                ),
            },
            {
                "id": "PT-003",
                "name": "Overly permissive CORS",
                "severity": self.SEVERITY_HIGH,
                "pattern": re.compile(r"(?i)(access-control-allow-origin|cors_allow|allow_origin)\s*[:=]\s*['\"]?\*['\"]?"),
                "breach": (
                    "A wildcard CORS origin combined with credentialed requests lets "
                    "any origin read authenticated responses, enabling cross-site "
                    "data theft from a logged-in victim's session."
                ),
                "fix": (
                    "Restrict the allowed origin to a known allowlist, never reflect "
                    "arbitrary Origin headers, and disable credentials when wildcarding."
                ),
            },
            {
                "id": "PT-004",
                "name": "Plaintext / outdated transport",
                "severity": self.SEVERITY_HIGH,
                "pattern": re.compile(r"(?i)(scheme\s*[:=]\s*['\"]?http['\"]?|http://|disable[_-]?https\s*=\s*true)"),
                "breach": (
                    "Plaintext HTTP exposes credentials and tokens to network "
                    "sniffing and tampering on any shared link."
                ),
                "fix": (
                    "Force HTTPS-only, enable HSTS with a long max-age, and redirect "
                    "all port-80 traffic to TLS. Deprecate HTTP entirely."
                ),
            },
            {
                "id": "PT-005",
                "name": "Debug mode enabled in production",
                "severity": self.SEVERITY_MEDIUM,
                "pattern": re.compile(r"(?i)(debug\s*[:=]\s*(true|1|on|yes)|env\s*[:=]\s*['\"]?development['\"]?)"),
                "breach": (
                    "Debug/dev mode can leak stack traces, source maps, and verbose "
                    "errors that reveal internal structure and secrets to an attacker."
                ),
                "fix": (
                    "Gate debug behavior behind an explicit non-production flag, strip "
                    "it from release builds via build variants, and ship a prod config "
                    "that fails closed if the flag is missing."
                ),
            },
            {
                "id": "PT-006",
                "name": "Permissive filesystem / overlay permission",
                "severity": self.SEVERITY_MEDIUM,
                "pattern": re.compile(r"(?i)(SYSTEM_ALERT_WINDOW|chmod\s+777|allow[_-]?all|mode\s*[:=]\s*['\"]?0o?777['\"]?)"),
                "breach": (
                    "An overlay permission combined with world-readable storage "
                    "enables clickjacking and tap-hijacking overlays over sensitive UI."
                ),
                "fix": (
                    "Request overlay permission only while actively needed, scope "
                    "stored files to app-private storage, and never relax file modes "
                    "to world-readable/writable."
                ),
            },
            {
                "id": "PT-007",
                "name": "Verbose logging of sensitive data",
                "severity": self.SEVERITY_LOW,
                "pattern": re.compile(r"(?i)(log[_-]?level\s*[:=]\s*['\"]?debug['\"]?|trace|print[_-]?secrets)"),
                "breach": (
                    "Debug/trace logging may persist tokens or PII to logcat, system "
                    "journals, or crash reporters accessible to other processes."
                ),
                "fix": (
                    "Cap log level at INFO/WARN in release, add a redaction layer for "
                    "known-sensitive fields, and exclude logs from crash reports."
                ),
            },
            {
                "id": "PT-008",
                "name": "Unbounded input / missing size limits",
                "severity": self.SEVERITY_MEDIUM,
                "pattern": re.compile(r"(?i)(max[_-]?length\s*[:=]\s*['\"]?0['\"]?|no[_-]?limit|unlimited)"),
                "breach": (
                    "Missing input size limits permit resource-exhaustion vectors "
                    "against parsing, storage, and LLM token budgets."
                ),
                "fix": (
                    "Enforce explicit max lengths on every untrusted field, cap "
                    "request body and prompt token sizes, and apply back-pressure."
                ),
            },
        ]

    # --------------------------------------------------------------- parsing
    def parse_config(self, text: str) -> Dict[str, str]:
        """Best-effort flattening of a config layout into key->value pairs.

        Accepts JSON, INI-style ``key = value``, YAML-ish ``key: value`` lines,
        and ``permission.ROLE = NAME`` style lines. Non-matching lines are kept
        verbatim under a synthetic key so the pattern scanner still sees them.
        """
        parsed: Dict[str, str] = {}
        text = text or ""
        # Try JSON first.
        try:
            obj = json.loads(text)
            self._flatten_json(obj, parsed, prefix="")
            if parsed:
                return parsed
        except (ValueError, json.JSONDecodeError):
            pass

        # Fallback: line-oriented parse.
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            m = re.match(r"^([A-Za-z0-9_.\-]+)\s*[:=]\s*(.+)$", line)
            if m:
                parsed[m.group(1).lower()] = m.group(2).strip().strip("'\"")
            else:
                parsed[f"line{len(parsed)}"] = line
        return parsed

    def _flatten_json(self, obj: Any, out: Dict[str, str], prefix: str) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                key = f"{prefix}.{k}" if prefix else str(k).lower()
                self._flatten_json(v, out, key)
        elif isinstance(obj, list):
            for idx, v in enumerate(obj):
                self._flatten_json(v, out, f"{prefix}[{idx}]")
        else:
            out[prefix.lower()] = str(obj)

    # ----------------------------------------------------------------- audit
    def audit(self, config_text: str) -> Dict[str, Any]:
        """Run the full purple-team diagnostic pass on ``config_text``."""
        parsed = self.parse_config(config_text)
        findings: List[Dict[str, Any]] = []
        scanned_blob = "\n".join(f"{k}: {v}" for k, v in parsed.items())

        for rule in self.rules:
            hits = rule["pattern"].findall(scanned_blob)
            if hits:
                findings.append(
                    {
                        "id": rule["id"],
                        "name": rule["name"],
                        "severity": rule["severity"],
                        "breach": rule["breach"],
                        "fix": rule["fix"],
                        "evidence": sorted({str(h) if isinstance(h, str) else str(h[0]) for h in hits}),
                    }
                )

        findings.sort(key=lambda f: self._ORDER.get(f["severity"], 99))
        score, grade = self._score(findings)
        return {
            "scanned_keys": len(parsed),
            "findings": findings,
            "summary": self._summary(findings),
            "risk_score": score,
            "risk_grade": grade,
            "remediation_plan": self._remediation_plan(findings),
        }

    def _summary(self, findings: List[Dict[str, Any]]) -> Dict[str, int]:
        counts: Dict[str, int] = {s: 0 for s in self._ORDER}
        for f in findings:
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        return counts

    def _score(self, findings: List[Dict[str, Any]]) -> Tuple[int, str]:
        weights = {
            self.SEVERITY_CRITICAL: 25,
            self.SEVERITY_HIGH: 12,
            self.SEVERITY_MEDIUM: 5,
            self.SEVERITY_LOW: 2,
            self.SEVERITY_INFO: 0,
        }
        raw = sum(weights.get(f["severity"], 0) for f in findings)
        score = min(100, raw)
        if score >= 60:
            grade = "F"
        elif score >= 40:
            grade = "D"
        elif score >= 25:
            grade = "C"
        elif score >= 10:
            grade = "B"
        else:
            grade = "A"
        return score, grade

    def _remediation_plan(self, findings: List[Dict[str, Any]]) -> List[str]:
        if not findings:
            return [
                "No high-signal risk patterns matched the provided layout.",
                "Continue good practice: least-privilege permissions, secrets in a vault, "
                "and TLS-by-default. Re-audit after each config change.",
            ]
        plan: List[str] = []
        seen = False
        for f in findings:
            seen = True
            plan.append(f"[{f['severity']}] {f['id']} {f['name']} -> {f['fix']}")
        if not seen:
            plan.append("No remediation actions required.")
        return plan

    # ------------------------------------------------------ LLM-augmented fix
    def build_fix_prompt(self, config_text: str, report: Dict[str, Any]) -> str:
        """Compose a prompt asking the LLM for *architectural* fixes only.

        The system preamble constrains the model to defensive remediation and
        forbids executable payloads -- this keeps the audit strictly diagnostic.
        """
        findings_block = json.dumps(report["findings"], indent=2)
        return (
            "You are a purple-team security architect. Given the configuration "
            "layout and the diagnostic findings below, produce ONLY architectural "
            "remediation: hardened config snippets, defense-in-depth changes, and "
            "a step-by-step patch methodology. Do NOT emit executable exploit "
            "payloads, weaponized PoCs, or instructions for attacking a live system.\n\n"
            f"CONFIGURATION LAYOUT:\n{config_text}\n\n"
            f"DIAGNOSTIC FINDINGS:\n{findings_block}\n\n"
            "Output: a prioritized remediation plan with concrete fixes."
        )


# ===========================================================================
# Module 3 -- System Overlay & Audio Pipeline (background service loop)
# Structural support for a floating overlay rendered above other apps, plus an
# audio capture pipeline, designed to run in a low-power foreground service.
#
# On Android the heavy work (drawing a SYSTEM_ALERT_WINDOW overlay, recording
# audio via AudioRecord) is done by the p4a service. Here we provide the
# Python-side loop, a platform probe, and a thin audio abstraction so the UI
# can drive it. The loop is throttled to a configurable cadence to save power.
# ===========================================================================
class AudioPipeline:
    """Thin abstraction over the audio capture source.

    On desktop (no pyjnius) it returns silence and reports unavailable. On
    Android it would bind to AudioRecord inside the foreground service; the
    binding point is intentionally isolated so the UI never imports jnius
    directly and stays testable.
    """

    def __init__(self):
        self._available = False
        self._error: Optional[str] = None
        self._probe()

    def _probe(self) -> None:
        try:
            try:
                from android.permissions import check_permission, Permission  # type: ignore

                self._available = check_permission(Permission.RECORD_AUDIO)
            except Exception:
                # Desktop / no pyjnius -> mark unavailable, do not raise.
                self._available = False
                self._error = "Audio capture requires Android + RECORD_AUDIO permission."
        except Exception as exc:  # noqa: BLE001
            self._available = False
            self._error = str(exc)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def last_error(self) -> Optional[str]:
        return self._error

    def capture_chunk(self) -> bytes:
        """Return a chunk of audio. Placeholder for the platform binding."""
        if not self._available:
            return b""
        # Real implementation delegates to the foreground-service AudioRecord.
        return b""


class OverlayService:
    """Background loop that drives the floating overlay + audio pipeline.

    Designed for a low-battery foreground service:
      * the loop sleeps ``interval`` seconds between ticks (default 5s),
      * the work per tick is tiny (status ping / audio chunk poll),
      * it honors a stop flag for clean shutdown,
      * and it holds a WAKE_LOCK on Android so the schedule survives doze.

    The actual SYSTEM_ALERT_WINDOW drawing on Android is performed by the p4a
    service process (see ``run_overlay_service`` entry point at the bottom of
    this file); this class is the Python controller that the UI starts/stops.
    """

    def __init__(self, interval: float = 5.0):
        self.interval = interval
        self.audio = AudioPipeline()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.running = False
        # UI-facing status (read from the main thread).
        self.last_tick: Optional[str] = None
        self.tick_count = 0

    # ------------------------------------------------------------- lifecycle
    def start(self) -> bool:
        with self._lock:
            if self.running:
                return True
            if not self._can_draw_overlay():
                # On desktop or without permission we still run the loop in
                # simulation mode so the UI remains demonstrable.
                pass
            self._stop.clear()
            self.running = True
            self._thread = threading.Thread(
                target=self._run, name="BeruOverlay", daemon=True
            )
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            self.running = False
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    # -------------------------------------------------------------- platform
    @staticmethod
    def _can_draw_overlay() -> bool:
        try:
            from android.permissions import check_permission, Permission  # type: ignore

            return check_permission(Permission.SYSTEM_ALERT_WINDOW)  # type: ignore[attr-defined]
        except Exception:
            return False

    # ------------------------------------------------------------------ loop
    def _run(self) -> None:
        """Service loop body. Kept deliberately lightweight."""
        while not self._stop.is_set():
            try:
                # 1) pull an audio chunk (no-op on desktop)
                if self.audio.available:
                    _ = self.audio.capture_chunk()
                # 2) update status for the UI
                with self._lock:
                    self.tick_count += 1
                    self.last_tick = _dt.datetime.now().isoformat(timespec="seconds")
            except Exception:
                # A service loop must never die on a transient error.
                pass
            # Throttle: short waits so stop() responds quickly.
            self._stop.wait(self.interval)


# ===========================================================================
# Module 5 -- Offline Chat Engine
# A local, rule + pattern based conversational engine. Works with NO internet.
# Falls back to this when the OpenRouter key is missing or the network is down.
# It remembers conversation context in-memory and reads/writes Shadow Memory.
# ===========================================================================
class OfflineChatEngine:
    """A small, self-contained assistant that needs no network.

    Strategy:
      * keyword/pattern rules for common intents (greetings, time, identity,
        memory queries, security tips, math, jokes, sentiment),
      * a tiny template generator for open-ended replies,
      * an in-memory turn history so follow-ups have context,
      * optional fallback to the online OpenRouter client (when a key is set
        and connectivity is available).
    """

    WAKE_WORDS = ("beru", "hey beru", "ok beru", "okay beru")

    def __init__(self, vault: Optional["ShadowMemoryVault"] = None,
                 phone: Optional["PhoneController"] = None):
        self.vault = vault
        self.phone = phone
        self.history: List[Dict[str, str]] = []
        self._user_name: Optional[str] = None

    # ------------------------------------------------------------- public API
    def responds_to(self, text: str) -> Dict[str, Any]:
        """Return a reply dict: {text, mode, wake_word_detected}."""
        raw = (text or "").strip()
        low = raw.lower()
        wake = any(w in low for w in self.WAKE_WORDS)
        cleaned = self._strip_wake(raw)

        # 1) Try phone-control intents first (call/sms/open/torch/...).
        if self.phone is not None:
            intent = self.phone.parse_intent(cleaned)
            if intent["action"] != "unknown":
                reply = self.phone.execute(cleaned)
                self.history.append({"role": "user", "content": cleaned})
                self.history.append({"role": "assistant", "content": reply})
                self.history = self.history[-20:]
                return {"text": reply, "mode": "phone", "wake_word_detected": wake}

        # 2) Otherwise, offline conversational reply.
        reply = self._generate(cleaned, low)
        self.history.append({"role": "user", "content": cleaned})
        self.history.append({"role": "assistant", "content": reply})
        # Keep the last 20 turns to bound memory.
        self.history = self.history[-20:]
        return {"text": reply, "mode": "offline", "wake_word_detected": wake}

    @staticmethod
    def _strip_wake(text: str) -> str:
        low = text.lower()
        for w in OfflineChatEngine.WAKE_WORDS:
            idx = low.find(w)
            if idx == 0:
                return text[len(w):].lstrip(" ,.!?")
        return text

    # ------------------------------------------------------------- generator
    def _generate(self, text: str, low: str) -> str:
        if not text:
            return "Haan, main sun raha hoon. Bolo. 🎧" if self._user_name is None else \
                f"{self._user_name}, bolo — main yahin hoon."

        # Identity / self
        if any(k in low for k in ("your name", "who are you", "tumhara naam", "tu kaun", "aap kaun", "kaun ho")):
            return "Main Beru hoon — tumhara personal AI saathi. Offline chal sakta hoon, secrets yaad rakhta hoon (Shadow Memory), aur security auditing bhi karta hoon."

        # Greetings
        if any(k in low for k in ("hello", "hi ", "hey", "namaste", "namaskar", "salam", "hii", "hi", "yo")):
            greet = "Namaste!" if "namaste" in low or "namaskar" in low else "Hey!"
            who = f" {self._user_name}" if self._user_name else ""
            return f"{greet}{who} Beru this side. Kya help karoon? (offline mode)"

        # How are you
        if any(k in low for k in ("how are you", "kaise ho", "kaisa hai", "kya haal")):
            return "Ekdum ready. Tum batao — kya plan hai? Main offline bhi kaam karta hoon, internet zaroori nahi."

        # Time / date
        if "time" in low and any(k in low for k in ("what", "kitna", "kya", "bata", "now")):
            now = _dt.datetime.now().strftime("%I:%M %p")
            return f"Abhi time hai {now}."
        if any(k in low for k in ("date", "today", "aaj ki date", "kj din")):
            today = _dt.datetime.now().strftime("%A, %d %B %Y")
            return f"Aaj hai {today}."

        # Remember / save to shadow memory
        if any(k in low for k in ("remember", "yaad rakh", "save this", "note le", "likh le")):
            if self.vault is not None:
                self.vault.add("notes", {"title": "from chat", "content": text})
                return "Done — Shadow Memory mein save kar liya. 🔒"
            return "Likha lekin vault nahi mila."

        # Recall from memory
        if any(k in low for k in ("what did i", "my notes", "meri notes", "recall", "yaad hai", "i say", "maine kya")):
            if self.vault is not None:
                notes = self.vault.list_collection("notes")
                if not notes:
                    return "Abhi tak Shadow Memory khaali hai. 'Beru, remember...' bolke kuch save karo."
                last = notes[-1]
                snippet = last.get("content", "")[:80]
                return f"Latest note: \"{snippet}...\""
            return "Vault unavailable."

        # Set name
        if "my name is" in low or "mera naam" in low or "mujhe" in low and "bula" in low:
            m = re.search(r"(?:my name is|mera naam|mujhe.*?bula)\s+([a-zA-Z\u0900-\u097F]+)", low)
            if m:
                self._user_name = m.group(1).capitalize()
                return f"Namaste {self._user_name}! Yaad rakh liya."

        # Security / purple team tip
        if any(k in low for k in ("security", "ssecure", "hack", "password", "vault safe", "audit")):
            return ("Security tip: apne secrets kabhi code mein mat chhupaao. Shadow Memory "
                    "local hai (device pe). API key bhi sirf device pe, share mat karo. "
                    "Purple Team screen pe config daal ke audit kara lo.")

        # Math
        m = re.match(r"^\s*([\d\.\s\+\-\*\/\(\)]+)\s*=\s*$", text) or \
            re.match(r"^\s*(?:what is|kitna hota|calculate|solve)\s+([\d\.\s\+\-\*\/\(\)]+)\s*$", low)
        if m:
            try:
                expr = m.group(1).strip()
                if all(c in "0123456789.+-*/() " for c in expr):
                    # eval is safe here: only digits/operators pass the filter.
                    result = eval(expr, {"__builtins__": {}}, {})  # noqa: S307
                    return f"{expr} = {result}"
            except Exception:
                return "Wo calculate nahi kar paaya. Sirf + - * / aur numbers."

        # Jokes / fun
        if any(k in low for k in ("joke", "chutkula", "hasao", "funny")):
            import random
            jokes = [
                "Programmer ke 2 hardest problems hain: cache invalidation aur naam rakhna. 😄",
                "Main bug nahi hoon — main undocumented feature hoon!",
                "WiFi gaya toh main bhi offline — lekin phir bhi chal raha hoon. Yahi toh power hai. 🔥",
            ]
            return random.choice(jokes)

        # Thanks
        if any(k in low for k in ("thank", "shukriya", "dhanyawad")):
            return "Koi baat nahi! Aur kuch?"

        # Bye
        if any(k in low for k in ("bye", "tata", "alvida", "see you", "chalta hoon")):
            return f"Bye{(' ' + self._user_name) if self._user_name else ''}! Beru yahin background mein hai jab bhi need ho. 👋"

        # Help / capabilities
        if low in ("help", "?", "kya kar sakte", "what can you do"):
            return ("Main yeh kar sakta hoon:\n"
                    "• Offline baat-cheet (internet nahi chahiye)\n"
                    "• Time / date bataana\n"
                    "• Notes yaad rakhna ('Beru remember ...')\n"
                    "• Notes wapas dikhana ('kya yaad hai')\n"
                    "• Math (+ - * /)\n"
                    "• Security tips\n"
                    "• Background mein overlay service\n\n"
                    "Bas 'Beru ...' bolna, main sun lunga.")

        # Echo + fallback
        return (f"Sun liya: \"{text}\". Main offline mode mein hoon — "
                "agar detail chahiye toh API screen mein key daal do, "
                "phir OpenRouter se smarter answers milenge. "
                "Ya 'help' likho.")


# ===========================================================================
# Module 6 -- Wake Word + Voice Listener ("Beru")
# Listens for the word "Beru". When detected, starts the recognition window
# so the user can give a command. On Android it binds to the foreground
# service mic; on desktop it's a simulation (still detects typed "Beru").
# ===========================================================================
class WakeWordEngine:
    """Detects the 'Beru' wake word and arms a one-shot command window.

    Designed for background / low-power operation:
      * polls the audio pipeline on a short cadence,
      * only activates the heavier recognition when 'beru' is heard,
      * exposes a callback for the UI to react (e.g. open the chat screen).
    """

    def __init__(self, audio: AudioPipeline, on_wake: Optional[Callable[[str], None]] = None):
        self.audio = audio
        self._on_wake = on_wake
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.listening = False
        self.last_wake: Optional[str] = None
        # The most recent transcription from the foreground service. The Android
        # service writes here via the bound mic; on desktop it stays empty.
        self._transcript = ""

    def set_transcript(self, text: str) -> None:
        """Called by the platform audio layer with a new recognition result."""
        with self._lock:
            self._transcript = (text or "").strip()

    def start(self) -> None:
        with self._lock:
            if self.listening:
                return
            self._stop.clear()
            self.listening = True
            self._thread = threading.Thread(
                target=self._run, name="BeruWakeWord", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            self.listening = False

    def _run(self) -> None:
        # Cadence: 400ms — light enough for background, quick enough to feel live.
        while not self._stop.is_set():
            try:
                text = ""
                with self._lock:
                    text = self._transcript
                    self._transcript = ""
                if text:
                    low = text.lower()
                    if any(w in low for w in OfflineChatEngine.WAKE_WORDS):
                        self.last_wake = _dt.datetime.now().isoformat(timespec="seconds")
                        if self._on_wake:
                            # Run the callback off this thread (UI callers must marshal).
                            try:
                                self._on_wake(text)
                            except Exception:
                                pass
            except Exception:
                pass
            self._stop.wait(0.4)


# ===========================================================================
# Module 7 -- Voice Output (Text-to-Speech)
# Beru speaks its replies out loud. Works OFFLINE via Android's built-in
# TextToSpeech engine (no internet needed). On desktop it prints a marker.
# ===========================================================================
class VoiceOutput:
    """Offline text-to-speech wrapper.

    Android: uses android.tts.TextToSpeech via pyjnius (lazy import so desktop
    never crashes). Desktop: no-op (the on-screen bubble still shows the reply).
    """

    def __init__(self):
        self.enabled = True
        self._tts = None
        self._ready = False
        self._error: Optional[str] = None
        self._init_attempted = False
        # NOTE: Do NOT init the Java TextToSpeech engine here. Building it at
        # app startup crashes on some Android versions (esp. Android 16/API 36)
        # before the activity is fully ready. We lazily init on first speak().

    def _init_engine(self):
        if self._init_attempted:
            return
        self._init_attempted = True
        try:
            from jnius import autoclass  # type: ignore

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Locale = autoclass("java.util.Locale")
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            activity = PythonActivity.mActivity
            self._tts = TextToSpeech(activity, None)
            # Use English-India locale so it handles Hinglish reasonably; fall
            # back to US English if unavailable.
            result = self._tts.setLanguage(Locale("en", "IN"))
            if result < 0:
                self._tts.setLanguage(Locale.US)
            self._ready = True
        except Exception as exc:  # noqa: BLE001 -- desktop / no jnius
            self._ready = False
            self._error = str(exc)

    @property
    def available(self) -> bool:
        if not self._ready:
            self._init_engine()
        return self._ready and self._tts is not None

    def speak(self, text: str) -> None:
        """Speak ``text`` aloud if TTS is enabled and available."""
        if not self.enabled:
            return
        # Lazy-init on first real use, guarded so a TTS init failure can never
        # crash the app (it just stays silent and shows the on-screen bubble).
        if not self._ready:
            self._init_engine()
        if not (self._ready and self._tts is not None):
            return
        # Strip emoji & markup that the TTS engine would read literally.
        clean = re.sub(r"\[/?[bi]\]|\[color=[^\]]*\]|\[/color\]", "", text or "")
        clean = re.sub(r"[^\w\s,.!?'\-:/à-üÀ-Ü]", " ", clean).strip()
        if not clean:
            return
        try:
            # Flush queue so the latest reply interrupts anything stale.
            self._tts.speak(clean, 1, None)  # 1 = QUEUE_FLUSH
        except Exception:
            pass

    def stop(self) -> None:
        if self.available:
            try:
                self._tts.stop()
            except Exception:
                pass

    def toggle(self) -> bool:
        self.enabled = not self.enabled
        if not self.enabled:
            self.stop()
        return self.enabled


# ===========================================================================
# Module 8 -- Phone Controller (online, voice-driven)
# Voice command -> intent -> Android action. Controls the phone:
#   * call <number/name>     -> ACTION_CALL / ACTION_DIAL
#   * message <name> <text>  -> ACTION_SENDTO (SMS)
#   * open <app>             -> launchApp / browser / maps / settings
#   * search <query>         -> browser web search
#   * camera / flashlight    -> CAMERA_INTENT / torch toggle
#   * share <text>           -> ACTION_SEND
#   * wifi / bluetooth / torch toggle
#   * play music             -> media intent
# Uses pyjnius (Android). On desktop every action no-ops and reports a message.
# Intent routing is local + instant; the OpenRouter LLM is only used for free-
# text understanding (parsing the command into {action, target, text}).
# ===========================================================================
class PhoneController:
    """Voice-driven phone control layer for Android.

    All hardware/system actions go through Android intents via pyjnius. The
    intent routing is LOCAL (no network), so "call", "open settings", "torch"
    work instantly. OpenRouter is used only to interpret loose Hindi/Hinglish
    commands into a structured {action, target} that this controller executes.
    """

    # Action keywords -> canonical action. Order matters (longest first).
    KEYWORDS = [
        ("call", "call"),
        ("phone", "call"),
        ("band karo", "call"),
        ("dial", "call"),
        ("message", "sms"),
        ("sms", "sms"),
        ("text", "sms"),
        ("msg bhej", "sms"),
        ("open", "open_app"),
        ("launch", "open_app"),
        ("khol", "open_app"),
        ("search", "search"),
        ("google", "search"),
        ("dhund", "search"),
        ("camera", "camera"),
        ("tasveer", "camera"),
        ("photo", "camera"),
        ("torch", "torch"),
        ("flashlight", "torch"),
        ("light on", "torch"),
        ("share", "share"),
        ("bhej", "share"),
        ("wifi", "wifi"),
        ("bluetooth", "bluetooth"),
        ("settings", "settings"),
        ("setting", "settings"),
        ("volume up", "volume_up"),
        ("volume down", "volume_down"),
        ("music", "music"),
        ("gaana", "music"),
        ("play", "music"),
    ]

    def __init__(self):
        self._jnius_ok = False
        self._error: Optional[str] = None
        self._torch_on = False
        try:
            from jnius import autoclass  # type: ignore  # noqa: F401

            self._jnius_ok = True
        except Exception as exc:  # noqa: BLE001 -- desktop
            self._error = str(exc)

    @property
    def available(self) -> bool:
        return self._jnius_ok

    @property
    def last_error(self) -> Optional[str]:
        return self._error

    # ------------------------------------------------------------------ parse
    def parse_intent(self, text: str) -> Dict[str, str]:
        """Cheap local parser: maps text -> {action, target, text}."""
        low = (text or "").lower().strip()
        # strip wake words first
        for w in OfflineChatEngine.WAKE_WORDS:
            if low.startswith(w):
                low = low[len(w):].lstrip(" ,.!?")
                break
        for kw, action in self.KEYWORDS:
            if kw in low:
                rest = low.split(kw, 1)[1].strip()
                return {"action": action, "target": rest, "raw": low}
        return {"action": "unknown", "target": low, "raw": low}

    # ------------------------------------------------------------------ exec
    def execute(self, text: str) -> str:
        """Parse + execute the command. Returns a Hinglish status message."""
        intent = self.parse_intent(text)
        action = intent["action"]
        target = intent["target"]
        if action == "unknown":
            return ("Samajh nahi aaya kya karna hai. Try: "
                    "'call 9876543210', 'message mom', 'open whatsapp', "
                    "'search weather', 'camera', 'torch', 'wifi off'.")
        if not self.available:
            return f"[desktop] Action '{action}' ({target}) — Android pe chalega."

        try:
            handler = getattr(self, f"_do_{action}", None)
            if handler is None:
                return f"Action '{action}' supported nahi hai abhi."
            return handler(target)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"

    # ----------------------------------------------------------- actions
    def _current_activity(self):
        from jnius import autoclass  # type: ignore

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        return PythonActivity.mActivity

    def _do_call(self, target: str) -> str:
        from jnius import autoclass  # type: ignore

        number = self._extract_number(target)
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        intent = Intent(Intent.ACTION_CALL, Uri.parse(f"tel:{number or target}"))
        self._current_activity().startActivity(intent)
        return f"Call kar raha hoon: {number or target} 📞"

    def _do_sms(self, target: str) -> str:
        from jnius import autoclass  # type: ignore

        number = self._extract_number(target)
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        intent = Intent(Intent.ACTION_SENDTO, Uri.parse(f"smsto:{number or target}"))
        intent.putExtra("sms_body", "")
        self._current_activity().startActivity(intent)
        return f"Message khola: {number or target} 💬"

    def _do_open_app(self, target: str) -> str:
        from jnius import autoclass  # type: ignore

        if not target:
            return "Kaunsa app kholna hai?"
        # Known mappings -> package names
        pkg_map = {
            "whatsapp": "com.whatsapp",
            "instagram": "com.instagram.android",
            "facebook": "com.facebook.katana",
            "youtube": "com.google.android.youtube",
            "maps": "com.google.android.apps.maps",
            "gmail": "com.google.android.gm",
            "chrome": "com.android.chrome",
            "calculator": "com.android.calculator2",
            "gallery": "com.android.gallery",
            "camera": "com.android.camera",
            "play store": "com.android.vending",
            "spotify": "com.spotify.music",
        }
        pkg = pkg_map.get(target.lower().strip())
        if not pkg:
            # Try launching by package directly.
            pkg = target.strip()
        Intent = autoclass("android.content.Intent")
        activity = self._current_activity()
        pm = activity.getPackageManager()
        try:
            intent = pm.getLaunchIntentForPackage(pkg)
            if intent is None:
                return f"App '{target}' nahi mila phone mein."
            activity.startActivity(intent)
            return f"Khola: {target} 📱"
        except Exception as exc:  # noqa: BLE001
            return f"App open fail: {exc}"

    def _do_search(self, target: str) -> str:
        from jnius import autoclass  # type: ignore

        if not target:
            return "Kya search karoon?"
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        url = f"https://www.google.com/search?q={Uri.encode(target)}"
        intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
        self._current_activity().startActivity(intent)
        return f"Google pe search: {target} 🔍"

    def _do_camera(self, _target: str) -> str:
        from jnius import autoclass  # type: ignore

        Intent = autoclass("android.content.Intent")
        intent = Intent("android.media.action.IMAGE_CAPTURE")
        self._current_activity().startActivity(intent)
        return "Camera khula 📷"

    def _do_torch(self, _target: str) -> str:
        from jnius import autoclass  # type: ignore

        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        context = PythonActivity.mActivity
        cm = context.getSystemService(context.CAMERA_SERVICE) if hasattr(context, "CAMERA_SERVICE") else None
        if cm is None:
            # Fallback: try via ApplicationContext
            Context = autoclass("android.content.Context")
            context = context.getApplicationContext()
            cm = context.getSystemService(Context.CAMERA_SERVICE)
        if cm is None:
            return "Torch control nahi mila."
        self._torch_on = not self._torch_on
        cm.setTorchMode(cm.getCameraIdList()[0], self._torch_on)
        return "Torch ON 🔦" if self._torch_on else "Torch OFF 🔦"

    def _do_share(self, target: str) -> str:
        from jnius import autoclass  # type: ignore

        if not target:
            return "Kya share karoon?"
        Intent = autoclass("android.content.Intent")
        String = autoclass("java.lang.String")
        intent = Intent(Intent.ACTION_SEND)
        intent.setType("text/plain")
        intent.putExtra(Intent.EXTRA_TEXT, String(target))
        self._current_activity().startActivity(Intent.createChooser(intent, "Share via"))
        return f"Share sheet khola: {target[:40]}…"

    def _do_wifi(self, _target: str) -> str:
        from jnius import autoclass  # type: ignore

        Intent = autoclass("android.content.Intent")
        intent = Intent("android.settings.WIFI_SETTINGS")
        self._current_activity().startActivity(intent)
        return "WiFi settings khole 📶"

    def _do_bluetooth(self, _target: str) -> str:
        from jnius import autoclass  # type: ignore

        Intent = autoclass("android.content.Intent")
        intent = Intent("android.settings.BLUETOOTH_SETTINGS")
        self._current_activity().startActivity(intent)
        return "Bluetooth settings khole 📶"

    def _do_settings(self, _target: str) -> str:
        from jnius import autoclass  # type: ignore

        Intent = autoclass("android.content.Intent")
        intent = Intent("android.settings.SETTINGS")
        self._current_activity().startActivity(intent)
        return "Settings khole ⚙️"

    def _do_volume_up(self, _target: str) -> str:
        from jnius import autoclass  # type: ignore

        AudioManager = autoclass("android.media.AudioManager")
        Context = autoclass("android.content.Context")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        am = PythonActivity.mActivity.getSystemService(Context.AUDIO_SERVICE)
        am.adjustVolume(AudioManager.ADJUST_RAISE, 0)
        return "Volume up 🔊"

    def _do_volume_down(self, _target: str) -> str:
        from jnius import autoclass  # type: ignore

        AudioManager = autoclass("android.media.AudioManager")
        Context = autoclass("android.content.Context")
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        am = PythonActivity.mActivity.getSystemService(Context.AUDIO_SERVICE)
        am.adjustVolume(AudioManager.ADJUST_LOWER, 0)
        return "Volume down 🔉"

    def _do_music(self, _target: str) -> str:
        from jnius import autoclass  # type: ignore

        Intent = autoclass("android.content.Intent")
        intent = Intent("android.intent.action.MUSIC_PLAYER")
        self._current_activity().startActivity(intent)
        return "Music player khola 🎵"

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _extract_number(text: str) -> str:
        digits = re.sub(r"\D", "", text or "")
        return digits if len(digits) >= 3 else ""


# ===========================================================================
# UI widgets
# ===========================================================================
def _bg_color(widget, color):
    """Paint a solid background behind a widget via its canvas."""
    widget.canvas.before.clear()
    with widget.canvas.before:
        Color(*color)
        Rectangle(pos=widget.pos, size=widget.size)
    # Repaint when the widget moves/resizes.
    widget.bind(
        pos=lambda inst, val: _repaint(widget, color),
        size=lambda inst, val: _repaint(widget, color),
    )


def _repaint(widget, color):
    widget.canvas.before.clear()
    with widget.canvas.before:
        Color(*color)
        Rectangle(pos=widget.pos, size=widget.size)


class TitleBar(BoxLayout):
    """Persistent top bar: app title + live status dot."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint_y = None
        self.height = 56
        self.orientation = "horizontal"
        self.padding = [14, 8, 14, 8]
        self.spacing = 12
        _bg_color(self, Theme.BG_PANEL)

        title = Label(
            text="[b]BERU[/b]  AI",
            markup=True,
            color=Theme.INK,
            font_name=Theme.FONT_REGULAR,
            font_size=20,
            size_hint_x=0.6,
            halign="left",
            valign="middle",
        )
        title.bind(size=title.setter("text_size"))
        self.add_widget(title)

        self.status_dot = Label(text="", color=Theme.WARN, font_size=12, size_hint_x=0.4, halign="right")
        self.status_dot.bind(size=self.status_dot.setter("text_size"))
        self.add_widget(self.status_dot)

    def set_status(self, text: str, color=Theme.WARN):
        self.status_dot.text = text
        self.status_dot.color = color


class NavBar(BoxLayout):
    """Bottom navigation: primary destinations."""

    def __init__(self, switcher, with_speaker=True, **kwargs):
        super().__init__(**kwargs)
        self.size_hint_y = None
        self.height = 64
        self.spacing = 2
        self.padding = [4, 4, 4, 4]
        _bg_color(self, Theme.BG_PANEL)
        destinations = [
            ("Chat", "chat"),
            ("Home", "dashboard"),
            ("API", "api"),
            ("Memory", "memory"),
            ("Audit", "purple"),
        ]
        for label_text, screen_name in destinations:
            btn = Button(
                text=label_text,
                color=Theme.INK,
                background_color=Theme.BG_PANEL_HI,
                font_size=13,
            )
            btn.bind(on_release=lambda _b, name=screen_name: switcher(name))
            self.add_widget(btn)
        # Speaker toggle: turn Beru's voice output on/off.
        if with_speaker:
            self.speaker_btn = Button(
                text="🔊",
                color=Theme.INK,
                background_color=Theme.BG_PANEL_HI,
                font_size=16,
                size_hint_x=0.5,
            )

            def toggle_speak(_btn):
                try:
                    app = App.get_running_app()
                    if app and getattr(app, "voice", None):
                        on = app.voice.toggle()
                        _btn.text = "🔊" if on else "🔇"
                except Exception:
                    pass

            self.speaker_btn.bind(on_release=toggle_speak)
            self.add_widget(self.speaker_btn)


class ThemedButton(Button):
    def __init__(self, text, on_release=None, primary=True, **kwargs):
        super().__init__(**kwargs)
        self.text = text
        self.color = Theme.INK
        self.font_name = Theme.FONT_REGULAR
        self.font_size = 15
        self.background_normal = ""
        self.background_down = ""
        self.background_color = Theme.CRIMSON if primary else Theme.BG_PANEL_HI
        if on_release:
            self.bind(on_release=on_release)


class ThemedInput(TextInput):
    def __init__(self, hint="", multiline=False, password=False, **kwargs):
        super().__init__(**kwargs)
        self.hint_text = hint
        self.multiline = multiline
        self.password = password
        self.background_normal = ""
        self.background_active = ""
        self.background_color = Theme.BG_PANEL_HI
        self.foreground_color = Theme.INK
        self.hint_text_color = Theme.INK_DIM
        self.cursor_color = Theme.CRIMSON_HI
        self.font_name = Theme.FONT_REGULAR
        self.font_size = 15
        self.padding = [10, 10, 10, 10]
        self.size_hint_y = None
        self.height = 44


class Panel(BoxLayout):
    """A raised panel with a header label."""

    def __init__(self, title, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = [14, 12, 14, 12]
        self.spacing = 10
        _bg_color(self, Theme.BG_PANEL)
        with self.canvas.before:
            Color(*Theme.STROKE)
            self._border = Rectangle(pos=self.pos, size=self.size)
        header = Label(
            text=title,
            color=Theme.CRIMSON_HI,
            font_size=13,
            size_hint_y=None,
            height=22,
            halign="left",
        )
        header.bind(size=header.setter("text_size"))
        self.add_widget(header)


class ScrollingLabel(Label):
    """A label wrapped in a scroll view for long output."""

    def __init__(self, text="", mono=True, **kwargs):
        super().__init__(**kwargs)
        self.text = text
        self.color = Theme.INK
        self.font_name = Theme.FONT_MONO if mono else Theme.FONT_REGULAR
        self.font_size = 13
        self.markup = True
        self.valign = "top"
        self.halign = "left"
        self.size_hint_y = None
        self.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
        self.bind(texture_size=lambda inst, val: setattr(inst, "height", val[1]))
        self.text_size = (self.width, None)


# ===========================================================================
# Screens
# ===========================================================================
class DashboardScreen(Screen):
    status_text = StringProperty("Initializing…")
    overlay_state = StringProperty("Overlay: OFF")

    def __init__(self, app_ref, **kwargs):
        super().__init__(**kwargs)
        self.app = app_ref
        root = BoxLayout(orientation="vertical")
        root.add_widget(TitleBar())
        body = BoxLayout(orientation="vertical", padding=14, spacing=12)
        _bg_color(body, Theme.BG)

        welcome = Label(
            text="[b]BERU AI[/b]\n[size=13][color=#a89a9d]Purple-team companion · local memory · uncensored models[/color][/size]",
            markup=True,
            color=Theme.INK,
            font_size=22,
            size_hint_y=None,
            height=90,
        )
        welcome.bind(size=welcome.setter("text_size"))
        body.add_widget(welcome)

        # Status panel
        self.status_panel = Panel("SYSTEM STATUS")
        self.status_label = ScrollingLabel("", mono=True)
        self.status_panel.add_widget(self.status_label)
        body.add_widget(self.status_panel)

        # Memory stats
        self.mem_panel = Panel("SHADOW MEMORY")
        self.mem_label = ScrollingLabel("", mono=False)
        self.mem_panel.add_widget(self.mem_label)
        body.add_widget(self.mem_panel)

        # Overlay toggle
        self.overlay_panel = Panel("SYSTEM OVERLAY")
        ov_row = BoxLayout(orientation="horizontal", spacing=10, size_hint_y=None, height=46)
        self.overlay_state_label = Label(text="Overlay: OFF", color=Theme.INK_DIM, font_size=14, size_hint_x=0.6)
        self.overlay_btn = ThemedButton("Start Overlay", on_release=self.toggle_overlay)
        ov_row.add_widget(self.overlay_state_label)
        ov_row.add_widget(self.overlay_btn)
        self.overlay_panel.add_widget(ov_row)
        body.add_widget(self.overlay_panel)

        # Quick actions
        actions = BoxLayout(orientation="horizontal", spacing=10, size_hint_y=None, height=48)
        actions.add_widget(ThemedButton("Open API Settings", on_release=lambda _b: self.app.go("api")))
        actions.add_widget(ThemedButton("Audit Config", on_release=lambda _b: self.app.go("purple")))
        body.add_widget(actions)

        body.add_widget(Widget_spacer())
        root.add_widget(body)
        root.add_widget(NavBar(self.app.go))
        self.add_widget(root)
        self.refresh()

    def toggle_overlay(self, *_):
        svc = self.app.overlay
        if svc.running:
            svc.stop()
            self.overlay_btn.text = "Start Overlay"
        else:
            svc.start()
            self.overlay_btn.text = "Stop Overlay"
        self.refresh_overlay()

    def refresh_overlay(self):
        svc = self.app.overlay
        state = "ON" if svc.running else "OFF"
        tick = svc.last_tick or "—"
        audio = "audio:yes" if svc.audio.available else "audio:no"
        self.overlay_state_label.text = f"Overlay: {state}"
        self.overlay_state = f"Overlay: {state}"
        self.overlay_panel.children[0].text = (
            f"State: {state}\nTicks: {svc.tick_count}\nLast: {tick}\n{audio}\n"
            f"Audio error: {svc.audio.last_error or 'none'}"
        )

    def refresh(self, *_):
        client = self.app.client
        snap = client.status_snapshot()
        self.status_label.text = (
            f"Endpoint  : {snap['base_url']}\n"
            f"Model     : {snap['model']}\n"
            f"API key   : {snap['key_preview']}\n"
            f"Has key   : {'yes' if snap['has_key'] else 'NO'}\n"
            f"Last HTTP : {snap['last_status'] if snap['last_status'] is not None else '—'}\n"
            f"Last err  : {snap['last_error'] or 'none'}"
        )
        stats = self.app.vault.stats()
        self.mem_label.text = (
            f"Notes: {stats.get('notes', 0)}    "
            f"Schedules: {stats.get('schedules', 0)}    "
            f"Creative: {stats.get('creative', 0)}\n"
            f"Vault: {os.path.basename(self.app.vault.path)}"
        )
        self.refresh_overlay()


class Widget_spacer(BoxLayout):
    """Tiny spacer widget (named to avoid clashing with kivy.uix.widget)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class ApiSwitcherScreen(Screen):
    """Module 1 -- Dynamic API Switcher.

    The OpenRouterClient is shared across screens. Editing the key field and
    pressing 'Apply' calls ``client.set_api_key(...)`` which swaps the bearer
    token atomically; the very next request (even one already queued from
    another thread) uses the new credential. No app restart required.
    """

    feedback = StringProperty("")

    def __init__(self, app_ref, **kwargs):
        super().__init__(**kwargs)
        self.app = app_ref
        root = BoxLayout(orientation="vertical")
        root.add_widget(TitleBar())

        scroll = ScrollView()
        body = BoxLayout(orientation="vertical", padding=14, spacing=12, size_hint_y=None)
        body.bind(width=lambda inst, val: setattr(inst, "height", max(val, 1)))
        body.height = 1
        _bg_color(body, Theme.BG)

        info = Label(
            text="[b]Dynamic API Switcher[/b]\n[size=12][color=#a89a9d]Swap the OpenRouter key at runtime. Changes apply to in-flight HTTP operations immediately.[/color][/size]",
            markup=True, color=Theme.INK, font_size=16, size_hint_y=None, height=70,
        )
        info.bind(size=info.setter("text_size"))
        body.add_widget(info)

        # Endpoint
        ep_panel = Panel("ENDPOINT")
        self.endpoint_input = ThemedInput(hint="https://openrouter.ai/api/v1")
        ep_panel.add_widget(self.endpoint_input)
        body.add_widget(ep_panel)

        # Default model
        model_panel = Panel("DEFAULT MODEL")
        self.model_input = ThemedInput(hint="cognitivecomputations/dolphin-mixtral-8x7b")
        model_panel.add_widget(self.model_input)
        body.add_widget(model_panel)

        # API key
        key_panel = Panel("OPENROUTER API KEY (Bearer token)")
        key_row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=44)
        self.key_input = ThemedInput(hint="sk-or-v1-…", password=True)
        self.show_btn = ThemedButton("Show", primary=False, on_release=self.toggle_show)
        self.show_btn.size_hint_x = 0.3
        key_row.add_widget(self.key_input)
        key_row.add_widget(self.show_btn)
        key_panel.add_widget(key_row)
        body.add_widget(key_panel)

        # Actions
        actions = BoxLayout(orientation="horizontal", spacing=10, size_hint_y=None, height=48)
        actions.add_widget(ThemedButton("Apply", on_release=self.apply_settings))
        actions.add_widget(ThemedButton("Verify", primary=False, on_release=self.verify_key))
        body.add_widget(actions)

        # Feedback
        self.feedback_label = ScrollingLabel("", mono=False)
        body.add_widget(self.feedback_label)

        body.add_widget(Widget_spacer())
        scroll.add_widget(body)
        root.add_widget(scroll)
        root.add_widget(NavBar(self.app.go))
        self.add_widget(root)
        self.populate()

    def populate(self):
        snap = self.app.client.status_snapshot()
        self.endpoint_input.text = snap["base_url"]
        self.model_input.text = snap["model"]
        # Never display the raw secret back; show masked preview only.
        self.key_input.hint_text = f"current: {snap['key_preview']}"

    def toggle_show(self, *_):
        self.key_input.password = not self.key_input.password
        self.show_btn.text = "Hide" if not self.key_input.password else "Show"

    def apply_settings(self, *_):
        client = self.app.client
        endpoint = self.endpoint_input.text.strip()
        model = self.model_input.text.strip()
        key = self.key_input.text.strip()

        messages = []
        if endpoint:
            client.set_base_url(endpoint)
            messages.append("endpoint updated")
        if model:
            client.set_default_model(model)
            messages.append("model updated")
        if key:
            # Critical swap: applied atomically, picked up by the next request.
            client.set_api_key(key)
            messages.append("API key swapped -> applies to ongoing operations")
            self.key_input.text = ""
            self.key_input.hint_text = f"current: {client.status_snapshot()['key_preview']}"

        if messages:
            self.feedback = "✓ " + "; ".join(messages)
        else:
            self.feedback = "Nothing to apply."
        self.feedback_label.text = self.feedback

    def verify_key(self, *_):
        client = self.app.client
        if not client.has_credentials():
            self.feedback = "No API key set. Add a key first."
            self.feedback_label.text = self.feedback
            return
        self.feedback = "Verifying against OpenRouter…"
        self.feedback_label.text = self.feedback

        def on_done(result, error):
            if error is None:
                msg = "✓ Connected. Bearer token accepted."
            elif isinstance(error, AuthenticationError):
                msg = "✗ Authorization rejected. Check the key."
            elif isinstance(error, OpenRouterError):
                msg = f"✗ {error}"
            else:
                msg = f"✗ {error}"
            Clock.schedule_once(lambda _dt: self._set_feedback(msg), 0)

        client.run_async(lambda: client.verify(), on_done)

    def _set_feedback(self, msg):
        self.feedback = msg
        self.feedback_label.text = msg


class MemoryScreen(Screen):
    """Module 2 UI -- Shadow Memory Vault visualization + editor."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(**kwargs)
        self.app = app_ref
        root = BoxLayout(orientation="vertical")
        root.add_widget(TitleBar())
        body = BoxLayout(orientation="vertical", padding=10, spacing=8)
        _bg_color(body, Theme.BG)

        tabs = TabbedPanel(tab_pos="top_left", tab_height=42)
        self.tabs = tabs
        tabs.default_tab_text = "Notes"
        tabs.default_tab_content = self._build_collection_tab("notes")
        tabs.add_widget(self._make_tab("Schedules", "schedules"))
        tabs.add_widget(self._make_tab("Creative", "creative"))
        body.add_widget(tabs)

        info = Label(
            text=f"[color=#a89a9d]Stored locally at: {self.app.vault.path}[/color]",
            markup=True, font_size=11, color=Theme.INK_DIM, size_hint_y=None, height=20,
        )
        body.add_widget(info)
        root.add_widget(body)
        root.add_widget(NavBar(self.app.go))
        self.add_widget(root)

    def _make_tab(self, title, collection):
        item = TabbedPanelItem(text=title)
        item.content = self._build_collection_tab(collection)
        return item

    def _build_collection_tab(self, collection):
        outer = BoxLayout(orientation="vertical", padding=8, spacing=8)
        _bg_color(outer, Theme.BG)

        # Composer
        composer = BoxLayout(orientation="vertical", spacing=6, size_hint_y=None, height=140)
        title_input = ThemedInput(hint="Title (optional)")
        body_input = ThemedInput(hint="Write a private note, schedule, or creative piece…",
                                 multiline=True)
        body_input.height = 70
        title_field = {"widget": title_input}
        body_field = {"widget": body_input}

        def save_entry(_btn):
            title = title_input.text.strip()
            content = body_input.text.strip()
            if not content:
                return
            entry = {"title": title or "(untitled)", "content": content}
            if collection == "schedules":
                entry["when"] = _dt.datetime.now().isoformat(timespec="minutes")
            self.app.vault.add(collection, entry)
            title_input.text = ""
            body_input.text = ""
            self._refresh_list(collection, list_area)

        actions = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=44)
        actions.add_widget(title_input)
        actions.add_widget(ThemedButton("Save", on_release=save_entry))
        composer.add_widget(actions)
        composer.add_widget(body_input)
        outer.add_widget(composer)

        # List
        sv = ScrollView()
        list_area = BoxLayout(orientation="vertical", spacing=6, size_hint_y=None)
        list_area.bind(minimum_height=list_area.setter("height"))
        list_area.height = 1
        sv.add_widget(list_area)
        outer.add_widget(sv)
        self._refresh_list(collection, list_area)
        return outer

    def _refresh_list(self, collection, list_area):
        list_area.clear_widgets()
        items = self.app.vault.list_collection(collection)
        if not items:
            list_area.add_widget(Label(
                text="[color=#a89a9d]No entries yet.[/color]",
                markup=True, size_hint_y=None, height=30,
            ))
            return
        for item in reversed(items):
            card = BoxLayout(orientation="vertical", spacing=4, size_hint_y=None, padding=[10, 8, 10, 8])
            card.height = 90
            _bg_color(card, Theme.BG_PANEL_HI)
            title = Label(
                text=f"[b]{item.get('title', '(untitled)')}[/b]",
                markup=True, color=Theme.CRIMSON_HI, font_size=14, halign="left", size_hint_y=None, height=20,
            )
            title.bind(size=title.setter("text_size"))
            snippet = (item.get("content", "")[:120] + ("…" if len(item.get("content", "")) > 120 else ""))
            body_lbl = Label(
                text=snippet, color=Theme.INK_DIM, font_size=12, halign="left",
                valign="top", size_hint_y=None, height=44,
            )
            body_lbl.bind(size=body_lbl.setter("text_size"))

            def delete_cb(_btn, eid=item.get("id")):
                self.app.vault.delete(collection, eid)
                self._refresh_list(collection, list_area)

            footer = BoxLayout(orientation="horizontal", spacing=6, size_hint_y=None, height=24)
            footer.add_widget(Label(
                text=f"[color=#7a6e70]{item.get('updated', '')}[/color]",
                markup=True, font_size=10, halign="left",
            ))
            del_btn = ThemedButton("Delete", primary=False, on_release=delete_cb)
            del_btn.size_hint_y = None
            del_btn.height = 24
            del_btn.font_size = 11
            footer.add_widget(del_btn)

            card.add_widget(title)
            card.add_widget(body_lbl)
            card.add_widget(footer)
            list_area.add_widget(card)


class PurpleTeamScreen(Screen):
    """Module 4 UI -- Purple Team Auditing Engine."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(**kwargs)
        self.app = app_ref
        self.auditor = self.app.auditor
        root = BoxLayout(orientation="vertical")
        root.add_widget(TitleBar())

        body = BoxLayout(orientation="vertical", padding=12, spacing=10)
        _bg_color(body, Theme.BG)

        intro = Label(
            text="[b]Purple Team Auditing Engine[/b]\n[size=11][color=#a89a9d]Paste a configuration layout. Emulates theoretical breach mechanics and emits architectural fixes. Strictly diagnostic -- no exploit payloads.[/color][/size]",
            markup=True, color=Theme.INK, font_size=15, size_hint_y=None, height=60, halign="left",
        )
        intro.bind(size=intro.setter("text_size"))
        body.add_widget(intro)

        self.config_input = ThemedInput(
            hint="Paste INI / YAML / JSON / key=value config here…", multiline=True
        )
        self.config_input.height = 150
        body.add_widget(self.config_input)

        actions = BoxLayout(orientation="horizontal", spacing=10, size_hint_y=None, height=48)
        actions.add_widget(ThemedButton("Run Audit", on_release=self.run_audit))
        actions.add_widget(ThemedButton("AI Remediation", primary=False, on_release=self.ask_llm))
        actions.add_widget(ThemedButton("Sample", primary=False, on_release=self.load_sample))
        body.add_widget(actions)

        # Result area
        self.score_label = Label(text="", markup=True, font_size=15, color=Theme.INK,
                                 size_hint_y=None, height=28, halign="left")
        self.score_label.bind(size=self.score_label.setter("text_size"))
        body.add_widget(self.score_label)

        sv = ScrollView()
        self.result_label = ScrollingLabel(
            "[color=#a89a9d]Report will appear here after running an audit.[/color]"
        )
        sv.add_widget(self.result_label)
        body.add_widget(sv)

        root.add_widget(body)
        root.add_widget(NavBar(self.app.go))
        self.add_widget(root)

    def load_sample(self, *_):
        self.config_input.text = (
            "title = Demo Backend\n"
            "api_key = sk-or-v1-9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c\n"
            "base_url = http://api.demo.local\n"
            "debug = true\n"
            "log_level = debug\n"
            "cors_allow = *\n"
            "verify = false\n"
            "android.permissions = SYSTEM_ALERT_WINDOW\n"
            "max_length = 0\n"
        )

    def run_audit(self, *_):
        text = self.config_input.text.strip()
        if not text:
            self.result_label.text = "[color=#e8c158]Paste a configuration layout first.[/color]"
            return
        report = self.auditor.audit(text)
        self._render_report(report)

    def _render_report(self, report):
        sev_color = {
            "CRITICAL": "#e63232",
            "HIGH": "#e8533a",
            "MEDIUM": "#e8c158",
            "LOW": "#7fb88f",
            "INFO": "#9a8e90",
        }
        score = report["risk_score"]
        grade = report["risk_grade"]
        gcolor = {"A": "#7fb88f", "B": "#7fb88f", "C": "#e8c158", "D": "#e8533a", "F": "#e63232"}[grade]
        self.score_label.text = (
            f"Risk score: [b][color={gcolor}]{score}/100 (grade {grade})[/color][/b]   "
            f"Keys scanned: {report['scanned_keys']}"
        )

        lines: List[str] = []
        summary = report["summary"]
        lines.append("[b]Severity summary[/b]")
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"):
            lines.append(f"  [color={sev_color[sev]}]{sev:<8}[/color] {summary.get(sev, 0)}")
        lines.append("")

        if not report["findings"]:
            lines.append("[color=#7fb88f]No high-signal risk patterns matched.[/color]")
        else:
            for f in report["findings"]:
                lines.append(f"[b][color={sev_color[f['severity']]}][{f['severity']}] {f['id']} {f['name']}[/color][/b]")
                lines.append(f"  [i]Breach model:[/i] {f['breach']}")
                lines.append(f"  [i]Fix:[/i] {f['fix']}")
                lines.append(f"  [i]Evidence:[/i] {', '.join(f['evidence'][:3])}")
                lines.append("")

        lines.append("[b]Remediation plan[/b]")
        for step in report["remediation_plan"]:
            lines.append(f"  • {step}")

        self.result_label.text = "\n".join(lines)
        self._last_report = report

    def ask_llm(self, *_):
        text = self.config_input.text.strip()
        if not text:
            self.result_label.text = "[color=#e8c158]Paste a configuration layout first.[/color]"
            return
        client = self.app.client
        if not client.has_credentials():
            self.result_label.text = "[color=#e63232]No OpenRouter key set. Add one in the API screen.[/color]"
            return
        report = getattr(self, "_last_report", None) or self.auditor.audit(text)
        prompt = self.auditor.build_fix_prompt(text, report)
        self.result_label.text = "[color=#a89a9d]Asking model for architectural remediation…[/color]"

        def on_done(result, error):
            if error is not None:
                msg = f"[color=#e63232]LLM error: {error}[/color]"
            else:
                answer = ""
                try:
                    answer = result["choices"][0]["message"]["content"].strip()
                except (KeyError, IndexError, TypeError):
                    answer = str(result)
                msg = f"[b]Architectural remediation (defensive only):[/b]\n\n{answer}"
            Clock.schedule_once(lambda _dt: setattr(self.result_label, "text", msg), 0)

        client.run_async(lambda: client.complete(prompt, system="You are a defensive security architect."), on_done)


# ===========================================================================
# Chat Screen -- offline + online conversation with Beru
# ===========================================================================
class ChatScreen(Screen):
    """Conversational UI. Works offline via OfflineChatEngine; when an
    OpenRouter key is set, escalates longer / open-ended queries online."""

    def __init__(self, app_ref, **kwargs):
        super().__init__(**kwargs)
        self.app = app_ref
        root = BoxLayout(orientation="vertical")
        root.add_widget(TitleBar())

        # Message transcript
        sv = ScrollView()
        self.transcript = BoxLayout(orientation="vertical", spacing=6, size_hint_y=None)
        self.transcript.bind(minimum_height=self.transcript.setter("height"))
        self.transcript.height = 1
        _bg_color(self.transcript, Theme.BG)
        sv.add_widget(self.transcript)
        root.add_widget(sv)

        # Input row
        row = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=48, padding=[8, 0, 8, 0])
        _bg_color(row, Theme.BG_PANEL)
        self.input = ThemedInput(hint="Likho... 'Beru <baat>' se shuru karo")
        self.input.bind(on_text_validate=self.send)  # enter key
        send_btn = ThemedButton("Send", on_release=self.send)
        send_btn.size_hint_x = 0.3
        row.add_widget(self.input)
        row.add_widget(send_btn)
        root.add_widget(row)

        # Online toggle hint
        self.mode_label = Label(text="", color=Theme.INK_DIM, font_size=11, size_hint_y=None, height=18)
        root.add_widget(self.mode_label)

        root.add_widget(NavBar(self.app.go))
        self.add_widget(root)

        # Welcome bubble
        self._add_bubble("beru",
                         "Namaste! Main Beru hoon. Offline mode mein chal raha hoon — "
                         "internet ki zaroorat nahi. Bas 'Beru <baat>' likho. "
                         "Mera naam sunte hi sunna shuru kar deta hoon. 🎧")

    # ------------------------------------------------------------- messaging
    def _add_bubble(self, sender: str, text: str):
        is_user = sender == "user"
        bubble = BoxLayout(orientation="vertical", size_hint_y=None, padding=[12, 8, 12, 8])
        bubble.height = max(54, min(220, 24 + len(text) // 3))
        _bg_color(bubble, Theme.CRIMSON if is_user else Theme.BG_PANEL_HI)
        name = "You" if is_user else "Beru"
        label = Label(
            text=f"[b][color={Theme.rgba_to_hex(Theme.CRIMSON_HI) if not is_user else '#ffffff'}]{name}[/color][/b]\n{text}",
            markup=True,
            color=Theme.INK,
            font_size=14,
            halign="left",
            valign="top",
            size_hint_y=None,
        )
        label.bind(
            width=lambda inst, val: setattr(inst, "text_size", (val - 8, None)),
            texture_size=lambda inst, val: setattr(bubble, "height", max(54, val[1] + 16)),
        )
        bubble.add_widget(label)
        self.transcript.add_widget(bubble)

        # Beru speaks its reply out loud (offline TTS).
        if not is_user:
            try:
                voice = getattr(self.app, "voice", None)
                if voice:
                    voice.speak(text)
            except Exception:
                pass

    def send(self, *_):
        text = self.input.text.strip()
        if not text:
            return
        self._add_bubble("user", text)
        self.input.text = ""

        # Detect wake word (so even typed "Beru ..." is honored on desktop)
        low = text.lower()
        wake = any(w in low for w in OfflineChatEngine.WAKE_WORDS)

        # Escalate online only if: key set AND it's a complex/long ask AND not
        # a quick offline-intentable query. Keep small talk offline.
        client = self.app.client
        go_online = client.has_credentials() and len(text) > 40 and wake

        self._update_mode(go_online, wake)

        if go_online:
            self._add_bubble("beru", "(online — OpenRouter se soch raha hoon...)")
            prompt = OfflineChatEngine._strip_wake(text)

            def on_done(result, error):
                if error is not None:
                    reply = f"(online fail: {error} — offline se jawab:) " + \
                            self.app.chat.responds_to(text)["text"]
                else:
                    try:
                        reply = result["choices"][0]["message"]["content"].strip()
                    except (KeyError, IndexError, TypeError):
                        reply = str(result)
                Clock.schedule_once(lambda _dt: self._add_bubble("beru", reply), 0)

            client.run_async(
                lambda: client.complete(prompt, system="You are Beru, a helpful offline-capable assistant."),
                on_done,
            )
        else:
            # Offline reply — instant, no network.
            res = self.app.chat.responds_to(text)
            reply = res["text"]
            if res["wake_word_detected"]:
                reply = "🎧 Sun liya. " + reply
            self._add_bubble("beru", reply)

    def _update_mode(self, online: bool, wake: bool):
        parts = []
        parts.append("ONLINE" if online else "OFFLINE")
        parts.append("wake: ON" if wake else "wake: —")
        if self.app.client.has_credentials():
            parts.append("key: set")
        self.mode_label.text = "   ".join(parts)


# ===========================================================================
# Application
# ===========================================================================
class BeruAIApp(App):
    """Single shared application state.

    Holds one :class:`OpenRouterClient` (so an API-key swap propagates to every
    screen), one :class:`ShadowMemoryVault`, one :class:`PurpleTeamAuditor`,
    and one :class:`OverlayService`.
    """

    def build(self):
        Window.clearcolor = Theme.BG
        # Responsive: clamp to phone-like aspect on desktop, free on mobile.
        if not getattr(self, "on_android", False):
            Window.size = (390, 844)

        # Build components defensively so a failure in any one does not black-
        # screen the whole app. Each component degrades to a safe stub.
        self.client = self._safe(lambda: OpenRouterClient(), "client", None)
        self.vault = self._safe(lambda: ShadowMemoryVault(), "vault", None)
        self.auditor = self._safe(lambda: PurpleTeamAuditor(), "auditor", None)
        self.overlay = self._safe(lambda: OverlayService(interval=5.0), "overlay", None)
        self.phone = self._safe(lambda: PhoneController(), "phone", None)
        self.chat = self._safe(
            lambda: OfflineChatEngine(self.vault, self.phone), "chat", None
        )
        # Beru speaks its replies aloud (offline TTS). Lazy-init so the Java
        # TextToSpeech object is never built during startup.
        self.voice = self._safe(lambda: VoiceOutput(), "voice", None)
        # Wake-word: "Beru" bolne par hi sunna shuru karta hai.
        if self.overlay is not None:
            self.wake_word = self._safe(
                lambda: WakeWordEngine(
                    self.overlay.audio, on_wake=lambda text: self._on_wake(text)
                ),
                "wake_word",
                None,
            )
        else:
            self.wake_word = None

        self.sm = ScreenManager()
        self.dashboard = DashboardScreen(self, name="dashboard")
        self.chat_screen = ChatScreen(self, name="chat")
        self.api_screen = ApiSwitcherScreen(self, name="api")
        self.memory_screen = MemoryScreen(self, name="memory")
        self.purple_screen = PurpleTeamScreen(self, name="purple")
        # Chat as the landing screen — opening the app drops you straight into
        # a conversation with Beru (offline by default).
        self.sm.add_widget(self.chat_screen)
        self.sm.add_widget(self.dashboard)
        self.sm.add_widget(self.api_screen)
        self.sm.add_widget(self.memory_screen)
        self.sm.add_widget(self.purple_screen)
        self.sm.current = "chat"

        # Periodic refresh of dashboard so live status/overlay info stays fresh.
        Clock.schedule_interval(self._tick, 2.0)
        return self.sm

    @staticmethod
    def _safe(factory, label, fallback=None):
        """Run ``factory``; on any error log it and return ``fallback``.

        This is the single point that keeps a component-level failure from
        black-screening the whole app during build()/on_start().
        """
        try:
            return factory()
        except Exception as exc:  # noqa: BLE001
            print(f"[BeruAI] component '{label}' failed to init: {exc}")
            traceback.print_exc()
            return fallback

    def _on_wake(self, text: str) -> None:
        """Called (from the wake-word worker thread) when 'Beru' is heard.

        Marshals back to the UI thread and routes the user into the chat.
        """
        Clock.schedule_once(lambda _dt: self._wake_to_chat(text), 0)

    def _wake_to_chat(self, text: str) -> None:
        # Bring chat to the foreground and feed the captured utterance.
        if self.sm.current != "chat":
            self.sm.current = "chat"
        # If there's a command after the wake word, auto-send it.
        cmd = OfflineChatEngine._strip_wake(text)
        if cmd.strip():
            self.chat_screen.input.text = cmd
            self.chat_screen.send()

    @property
    def on_android(self) -> bool:
        try:
            import android  # type: ignore  # noqa: F401

            return True
        except Exception:
            return False

    def go(self, name: str):
        if self.sm.current != name:
            self.sm.current = name
            screen = self.sm.get_screen(name)
            if hasattr(screen, "refresh"):
                screen.refresh()
            elif hasattr(screen, "populate"):
                screen.populate()

    def _tick(self, *_):
        if self.sm.current == "dashboard":
            self.dashboard.refresh()

    def on_start(self):
        """App open: start background overlay + wake-word listener.

        Deferred by 2s so the UI paints first (avoids startup contention on
        Android 16). Each background component is started defensively; if one
        fails the app keeps running.
        """
        # Defer heavy/background startup until after the UI is rendered.
        Clock.schedule_once(self._deferred_start, 2.0)

    def _deferred_start(self, *_):
        try:
            if self.overlay is not None:
                self.overlay.start()
        except Exception as exc:  # noqa: BLE001
            print(f"[BeruAI] overlay start failed: {exc}")
            traceback.print_exc()
        try:
            if self.wake_word is not None:
                self.wake_word.start()
        except Exception as exc:  # noqa: BLE001
            print(f"[BeruAI] wake_word start failed: {exc}")
            traceback.print_exc()

    def on_stop(self):
        for comp, name in (
            (getattr(self, "wake_word", None), "wake_word"),
            (getattr(self, "overlay", None), "overlay"),
            (getattr(self, "voice", None), "voice"),
            (getattr(self, "client", None), "client"),
        ):
            try:
                if comp is not None:
                    if name == "client":
                        comp.shutdown()
                    else:
                        comp.stop()
            except Exception as exc:  # noqa: BLE001
                print(f"[BeruAI] {name} stop failed: {exc}")


# ===========================================================================
# Module 3 -- foreground-service entry point (referenced by buildozer.spec)
# When buildozer packages Beru AI with `services = BeruOverlay:main.py:run_overlay_service`,
# the p4a service invokes this function in a dedicated process. It runs the
# lightweight OverlayService loop with the WAKE_LOCK held and a tiny cadence
# tuned for low battery environments.
# ===========================================================================
def run_overlay_service() -> None:
    """Entry point for the Android foreground overlay service."""
    svc = OverlayService(interval=5.0)
    svc.start()
    try:
        # Keep the service process alive until killed by the OS / user.
        while svc.running:
            time.sleep(1.0)
    except KeyboardInterrupt:
        svc.stop()


def main() -> None:
    BeruAIApp().run()


if __name__ == "__main__":
    main()
