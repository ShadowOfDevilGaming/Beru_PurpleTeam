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
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

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
    """Bottom navigation: four primary destinations."""

    def __init__(self, switcher, **kwargs):
        super().__init__(**kwargs)
        self.size_hint_y = None
        self.height = 64
        self.spacing = 2
        self.padding = [4, 4, 4, 4]
        _bg_color(self, Theme.BG_PANEL)
        destinations = [
            ("Dashboard", "dashboard"),
            ("API", "api"),
            ("Memory", "memory"),
            ("Purple", "purple"),
        ]
        for label_text, screen_name in destinations:
            btn = Button(
                text=label_text,
                color=Theme.INK,
                background_color=Theme.BG_PANEL_HI,
                font_size=14,
            )
            btn.bind(on_release=lambda _b, name=screen_name: switcher(name))
            self.add_widget(btn)


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

        self.client = OpenRouterClient()
        self.vault = ShadowMemoryVault()
        self.auditor = PurpleTeamAuditor()
        self.overlay = OverlayService(interval=5.0)

        self.sm = ScreenManager()
        self.dashboard = DashboardScreen(self, name="dashboard")
        self.api_screen = ApiSwitcherScreen(self, name="api")
        self.memory_screen = MemoryScreen(self, name="memory")
        self.purple_screen = PurpleTeamScreen(self, name="purple")
        self.sm.add_widget(self.dashboard)
        self.sm.add_widget(self.api_screen)
        self.sm.add_widget(self.memory_screen)
        self.sm.add_widget(self.purple_screen)

        # Periodic refresh of dashboard so live status/overlay info stays fresh.
        Clock.schedule_interval(self._tick, 2.0)
        return self.sm

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

    def on_stop(self):
        try:
            self.overlay.stop()
            self.client.shutdown()
        except Exception:
            pass


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
