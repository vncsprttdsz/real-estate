"""Cliente HTTP que imita o Chrome (fingerprint TLS) para reduzir bloqueios anti-bot."""
from __future__ import annotations

import random
import time

from .common import UA

try:
    from curl_cffi import requests as _requests

    def _session():
        return _requests.Session(impersonate="chrome")
except ImportError:  # pragma: no cover
    import requests as _requests

    def _session():
        return _requests.Session()


class Http:
    def __init__(self, min_delay: float = 1.0, max_delay: float = 2.5):
        self.s = _session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        })
        self.min_delay, self.max_delay = min_delay, max_delay
        self._last = 0.0

    def _wait(self):
        delay = random.uniform(self.min_delay, self.max_delay)
        elapsed = time.time() - self._last
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last = time.time()

    def request(self, method: str, url: str, retries: int = 3, **kw):
        kw.setdefault("timeout", 30)
        err = None
        for attempt in range(retries):
            self._wait()
            try:
                r = self.s.request(method, url, **kw)
                if r.status_code in (429, 500, 502, 503, 504):
                    err = RuntimeError(f"HTTP {r.status_code} em {url}")
                    time.sleep(5 * (attempt + 1))
                    continue
                return r
            except Exception as e:  # rede instável
                err = e
                time.sleep(3 * (attempt + 1))
        raise err

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)
