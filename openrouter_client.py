from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "cognitivecomputations/dolphin-mixtral-8x7b"
DEFAULT_TIMEOUT = 30.0
USER_AGENT = "BeruAI/1.0 (+https://openrouter.ai)"

class OpenRouterError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body

class AuthenticationError(OpenRouterError):
    pass

class RateLimitError(OpenRouterError):
    pass

class OpenRouterClient:
    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        default_model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        max_workers: int = 4,
    ):
        self._lock = threading.RLock()
        self._api_key = (api_key or "").strip()
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._default_model = default_model or DEFAULT_MODEL
        self._timeout = float(timeout)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._last_error: Optional[str] = None
        self._last_status: Optional[int] = None

    def set_api_key(self, api_key: str) -> None:
        with self._lock:
            self._api_key = (api_key or "").strip()

    def get_api_key(self) -> str:
        with self._lock:
            return self._api_key

    def set_base_url(self, base_url: str) -> None:
        with self._lock:
            self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")

    def get_base_url(self) -> str:
        with self._lock:
            return self._base_url

    def set_default_model(self, model: str) -> None:
        with self._lock:
            self._default_model = model or DEFAULT_MODEL

    def get_default_model(self) -> str:
        with self._lock:
            return self._default_model

    def has_credentials(self) -> bool:
        with self._lock:
            return bool(self._api_key)

    def status_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "base_url": self._base_url,
                "model": self._default_model,
                "has_key": bool(self._api_key),
                "key_preview": self._mask_key(self._api_key),
                "last_status": self._last_status,
                "last_error": self._last_error,
            }

    @staticmethod
    def _mask_key(key: str) -> str:
        if not key:
            return "—"
        if len(key) <= 8:
            return "*" * len(key)
        return key[:4] + "…" + key[-4:]

    def _snapshot(self) -> Dict[str, str]:
        with self._lock:
            return {
                "api_key": self._api_key,
                "base_url": self._base_url,
                "model": self._default_model,
            }

    def _headers(self, api_key: str) -> Dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "HTTP-Referer": "https://beru.ai/app",
            "X-Title": "Beru AI",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        snap: Dict[str, str],
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = snap["base_url"] + path
        if params:
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                url += "?" + urllib.parse.urlencode(filtered)

        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url, data=body, headers=self._headers(snap["api_key"]), method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                with self._lock:
                    self._last_status = getattr(resp, "status", None)
                    self._last_error = None
                if not raw:
                    return None
                ctype = resp.headers.get("Content-Type", "")
                if "json" in ctype:
                    return json.loads(raw)
                return raw
        except urllib.error.HTTPError as exc:
            text = ""
            try:
                text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            self._record_error(exc.code, text)
            if exc.code in (401, 403):
                raise AuthenticationError(f"Authorization rejected ({exc.code}). Verify the API key.", exc.code, text)
            if exc.code == 429:
                raise RateLimitError("Rate limited by the gateway.", exc.code, text)
            raise OpenRouterError(f"HTTP {exc.code} from {url}", exc.code, text)
        except urllib.error.URLError as exc:
            reason = exc.reason
            self._record_error(None, str(reason))
            raise OpenRouterError(f"Network error: {reason}")
        except TimeoutError:
            self._record_error(None, "timeout")
            raise OpenRouterError(f"Request timed out after {self._timeout}s")

    def _record_error(self, status: Optional[int], message: str) -> None:
        with self._lock:
            self._last_status = status
            self._last_error = message

    def list_models(self) -> List[Dict[str, Any]]:
        snap = self._snapshot()
        data = self._request("GET", "/models", snap)
        if isinstance(data, dict) and "data" in data:
            return list(data["data"])
        return data or []

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        snap = self._snapshot()
        payload: Dict[str, Any] = {
            "model": model or snap["model"],
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if extra:
            payload.update(extra)
        return self._request("POST", "/chat/completions", snap, payload)

    def complete(self, prompt: str, system: Optional[str] = None, **kwargs: Any) -> str:
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        result = self.chat(messages, **kwargs)
        try:
            return result["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError):
            return ""

    def verify(self) -> bool:
        snap = self._snapshot()
        self._request("GET", "/models", snap)
        return True

    def chat_async(
        self,
        messages: List[Dict[str, str]],
        callback: Callable[[Any, Optional[Exception]], None],
        **kwargs: Any,
    ) -> None:
        snap = self._snapshot()

        def _work() -> None:
            try:
                payload: Dict[str, Any] = {
                    "model": kwargs.pop("model", snap["model"]),
                    "messages": messages,
                    "temperature": kwargs.pop("temperature", 0.7),
                }
                if "max_tokens" in kwargs:
                    payload["max_tokens"] = kwargs.pop("max_tokens")
                if kwargs:
                    payload.update(kwargs)
                result = self._request("POST", "/chat/completions", snap, payload)
                callback(result, None)
            except Exception as exc:
                callback(None, exc)

        # Patched cleanly by Devil: Fixed the broken equal syntax error here
        self._executor.submit(_work)

    def run_async(
        self,
        work: Callable[[], Any],
        callback: Callable[[Any, Optional[Exception]], None],
    ) -> None:
        self._snapshot()

        def _wrap() -> None:
            try:
                result = work()
                callback(result, None)
            except Exception as exc:
                callback(None, exc)

        self._executor.submit(_wrap)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)

__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "OpenRouterClient",
    "OpenRouterError",
    "AuthenticationError",
    "RateLimitError",
]