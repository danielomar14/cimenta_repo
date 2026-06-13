"""
CIMENTA · M1 Ingesta · Sesión CloakBrowser (para portales tras Cloudflare).

Adaptado del repo Prueba_fotmob: lanza un Chromium stealth (cloakbrowser),
hace un warmup que resuelve el Cloudflare Turnstile, y reutiliza la sesión
(cookies cf_clearance) para traer el HTML renderizado de cualquier URL del sitio.

Uso:
    with BrowserSession(warmup_url="https://www.inmuebles24.com/") as b:
        html = b.get_html("https://www.inmuebles24.com/departamentos-en-venta-en-benito-juarez.html")
"""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

logger = logging.getLogger("browser")


class BrowserSession:
    def __init__(self, warmup_url: str, headless: bool = True,
                 proxy: Optional[str] = None, fingerprint: int = 42069,
                 settle: float = 6.0):
        from cloakbrowser import launch  # type: ignore
        kwargs: dict = {
            "headless": headless,
            "humanize": False,
            "args": [f"--fingerprint={fingerprint}"],
        }
        if proxy:
            kwargs["proxy"] = proxy
            kwargs["geoip"] = True
        logger.info("Lanzando CloakBrowser...")
        self._browser = launch(**kwargs)
        self._page = self._browser.new_page()
        self._warmup_url = warmup_url
        self._settle = settle
        self._warm = False

    def warmup(self) -> None:
        if self._warm:
            return
        logger.info("Warmup (resolviendo Cloudflare): %s", self._warmup_url)
        self._page.goto(self._warmup_url, timeout=60000, wait_until="domcontentloaded")
        time.sleep(self._settle)
        self._warm = True
        logger.info("Cloudflare resuelto. Sesión lista.")

    def get_html(self, url: str, wait: tuple[float, float] = (2.5, 4.5)) -> str:
        if not self._warm:
            self.warmup()
        self._page.goto(url, timeout=60000, wait_until="domcontentloaded")
        time.sleep(random.uniform(*wait))
        return self._page.evaluate("() => document.documentElement.outerHTML")

    def title(self) -> str:
        try:
            return self._page.evaluate("() => document.title")
        except Exception:
            return ""

    def close(self) -> None:
        try:
            self._browser.close()
        except Exception:
            pass

    def __enter__(self):
        self.warmup()
        return self

    def __exit__(self, *_):
        self.close()
