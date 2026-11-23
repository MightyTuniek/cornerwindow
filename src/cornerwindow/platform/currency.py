"""Exchange rates for '100 usd to eur', fetched only when you type a conversion and cached on
disk for half a day. The default source is frankfurter.dev (ECB reference rates, no key)."""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Callable

from ..core import log, tasks

_log = log.get("currency")
FRESH_S = 12 * 3600


class Rates:
    def __init__(self, path: Path, url: str) -> None:
        self.path = path
        self.url = url
        self.rates: dict[str, float] = {}
        self.date = ""
        self.fetched = 0.0
        self._fetching = False
        self._failed_at = 0.0
        try:
            data = json.loads(path.read_text())
            self.rates = {k: float(v) for k, v in data.get("rates", {}).items()}
            self.date, self.fetched = str(data.get("date", "")), float(data.get("fetched", 0))
        except (OSError, ValueError, AttributeError):
            pass

    def fresh(self) -> bool:
        return bool(self.rates) and time.time() - self.fetched < FRESH_S

    def ensure(self, then: Callable[[], None]) -> None:
        """Fetch in the background if stale; `then` runs on the main thread when new rates arrive."""
        if not self.url or self.fresh() or self._fetching or time.time() - self._failed_at < 60:
            return
        self._fetching = True

        def fetch() -> dict:
            request = urllib.request.Request(self.url, headers={"User-Agent": "CornerWindow"})
            with urllib.request.urlopen(request, timeout=6) as response:
                return json.loads(response.read().decode())

        def done(data: dict) -> None:
            self._fetching = False
            rates = data.get("rates") if isinstance(data, dict) else None
            if not rates:
                self._failed_at = time.time()
                return
            base = str(data.get("base", "EUR")).upper()
            if base != "EUR":  # normalise to "per 1 EUR"
                eur = float(rates.get("EUR", 0)) or None
                if eur is None:
                    self._failed_at = time.time()
                    return
                rates = {k: float(v) / eur for k, v in rates.items() if k != "EUR"}
                rates[base] = 1 / eur
            self.rates = {k.upper(): float(v) for k, v in rates.items()}
            self.date, self.fetched = str(data.get("date", "")), time.time()
            try:
                self.path.write_text(json.dumps({"rates": self.rates, "date": self.date, "fetched": self.fetched}))
            except OSError:
                pass
            then()

        def failed(exc) -> None:
            self._fetching = False
            self._failed_at = time.time()
            _log.info("Could not fetch exchange rates: %s", exc)

        tasks.run_async(fetch, done, error=failed)
