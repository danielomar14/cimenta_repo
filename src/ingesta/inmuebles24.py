"""
CIMENTA · M1 Ingesta · Portal: Inmuebles24 (Nivel B — vía CloakBrowser)

Inmuebles24 está tras Cloudflare. Se usa CloakBrowser (ver src/ingesta/browser.py)
para resolver el challenge una vez (warmup) y reutilizar la sesión.

Los anuncios viven en el DOM (React) como tarjetas data-qa="posting PROPERTY"
(clasificados) o "posting DEVELOPMENT" (desarrollos/preventa). Cada una tiene:
  data-id · POSTING_CARD_PRICE · dirección + colonia/alcaldía · POSTING_CARD_FEATURES
  (m²/rec/baños/estac) · href al aviso.
NO trae coordenadas → se geocodifica después por colonia (src/ingesta/geocode.py).

  URL: /{tipo}-en-{operacion}-en-{municipio}[-pagina-N].html   (30/pág)

Uso:
    python -m src.ingesta.inmuebles24                        # todo (config)
    python -m src.ingesta.inmuebles24 --municipio benito-juarez --tipo departamentos --operacion venta --max-pages 2
    python -m src.ingesta.inmuebles24 --no-headless          # ver el navegador
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

try:
    from tqdm import tqdm
except ImportError:
    class tqdm:  # type: ignore
        def __init__(self, it, **k): self.it = it
        def __iter__(self): return iter(self.it)
        def set_description(self, *a, **k): pass
        def set_postfix(self, *a, **k): pass
        def close(self): pass

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config.yml"
RAW_DIR = ROOT / "data" / "raw" / "inmuebles24"
PROC_DIR = ROOT / "data" / "processed"
CHECKPOINT = RAW_DIR / "_progress.json"
PORTAL = "inmuebles24"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(PORTAL)


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["ingesta"][PORTAL]


# ── Parsers de tarjeta ────────────────────────────────────────────────────────────
_CUR = {"MN": "MXN", "MXN": "MXN", "USD": "USD", "U$D": "USD", "US$": "USD"}


def parse_price(text: str | None) -> tuple[float | None, str | None, bool]:
    if not text:
        return None, None, False
    desde = "desde" in text.lower()
    cur = None
    for k, v in _CUR.items():
        if k in text:
            cur = v
            break
    m = re.search(r"([\d][\d,\.]*\d)", text)
    val = None
    if m:
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            val = None
    return val, cur, desde


def _num_before(text: str, unit_re: str):
    m = re.search(r"(\d+)\s*" + unit_re, text)
    return int(m.group(1)) if m else None


def parse_feats(text: str | None) -> dict:
    if not text:
        return {}
    return {
        "surface": _num_before(text, r"m²"),
        "rooms": _num_before(text, r"rec"),
        "bathrooms": _num_before(text, r"baño"),
        "parking": _num_before(text, r"estac"),
    }


def parse_loc(text: str | None) -> tuple[str | None, str | None]:
    """'Del Valle Sur, Benito Juárez' → (colonia, municipio)."""
    if not text:
        return None, None
    parts = [p.strip() for p in text.split(",")]
    if len(parts) >= 2:
        return ", ".join(parts[:-1]), parts[-1]
    return None, parts[0]


def _txt(el):
    return el.get_text(" ", strip=True) if el else None


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('[data-qa="posting PROPERTY"], [data-qa="posting DEVELOPMENT"]')
    out = []
    for c in cards:
        pid = c.get("data-id")
        if not pid:
            continue
        card_type = (c.get("data-qa") or "").replace("posting", "").strip() or "PROPERTY"
        price_raw = _txt(c.select_one('[data-qa="POSTING_CARD_PRICE"]'))
        precio, cur, desde = parse_price(price_raw)
        addr = _txt(c.select_one('[class*="location-address"]'))
        loc = _txt(c.select_one('[data-qa="POSTING_CARD_LOCATION"]'))
        colonia, municipio = parse_loc(loc)
        feats = parse_feats(_txt(c.select_one('[data-qa="POSTING_CARD_FEATURES"]')))
        a = c.select_one('a[href]')
        href = a.get("href").split("?")[0] if a and a.get("href") else None
        out.append({
            "id": pid, "card_type": card_type,
            "precio": precio, "currency": cur, "precio_desde": desde,
            "address": addr, "colonia": colonia, "municipio": municipio,
            "url": href,
            **feats,
        })
    return out


# ── Storage ───────────────────────────────────────────────────────────────────────
CSV_FIELDS = [
    "id", "portal", "tipo", "operacion", "card_type", "municipio", "colonia",
    "address", "currency", "precio", "precio_desde", "surface", "rooms",
    "bathrooms", "parking", "lat", "lng", "url",
]


def normalize(it: dict, tipo: str, operacion: str, base_url: str) -> dict:
    url = it.get("url")
    return {
        "id": it["id"], "portal": PORTAL, "tipo": tipo, "operacion": operacion,
        "card_type": it.get("card_type"), "municipio": it.get("municipio"),
        "colonia": it.get("colonia"), "address": it.get("address"),
        "currency": it.get("currency"), "precio": it.get("precio"),
        "precio_desde": it.get("precio_desde"), "surface": it.get("surface"),
        "rooms": it.get("rooms"), "bathrooms": it.get("bathrooms"),
        "parking": it.get("parking"), "lat": "", "lng": "",
        "url": f"{base_url}{url}" if url else "",
    }


def load_ck() -> dict:
    return json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else {"done": [], "counts": {}}


def save_ck(ck: dict):
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(ck, indent=2, ensure_ascii=False))


# ── Orquestación ─────────────────────────────────────────────────────────────────
def page_url(base: str, tipo_slug: str, op_slug: str, muni: str, page: int) -> str:
    suffix = f"-pagina-{page}" if page > 1 else ""
    return f"{base}/{tipo_slug}-en-{op_slug}-en-{muni}{suffix}.html"


def run(args):
    from src.ingesta.browser import BrowserSession

    cfg = load_config()
    base = cfg["base_url"].rstrip("/")
    max_pages = args.max_pages or int(cfg.get("max_pages", 40))
    tipos = cfg["tipos"]; ops = cfg["operaciones"]; munis = cfg["municipios"]

    sel_tipos = {args.tipo: tipos.get(args.tipo, args.tipo)} if args.tipo else tipos
    sel_ops = {args.operacion: ops.get(args.operacion, args.operacion)} if args.operacion else ops
    sel_munis = [args.municipio] if args.municipio else munis
    combos = [(m, ts, tl, os_, ol) for m in sel_munis for ts, tl in sel_tipos.items() for os_, ol in sel_ops.items()]

    RAW_DIR.mkdir(parents=True, exist_ok=True); PROC_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    raw_path = RAW_DIR / f"postings_{stamp}.jsonl"
    csv_path = PROC_DIR / "inmuebles24.csv"

    ck = load_ck(); done = set(tuple(x) for x in ck["done"]); seen: set = set()
    if raw_path.exists():
        with raw_path.open(encoding="utf-8") as f:
            for line in f:
                try: seen.add(json.loads(line)["id"])
                except Exception: pass

    raw_f = raw_path.open("a", encoding="utf-8")
    new_csv = not csv_path.exists()
    csv_f = csv_path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_f, fieldnames=CSV_FIELDS)
    if new_csv: writer.writeheader()

    total_new = 0
    browser = BrowserSession(warmup_url=cfg["warmup_url"], headless=not args.no_headless,
                             settle=float(cfg.get("settle", 6.0)))
    pbar = tqdm(combos, unit="combo", desc="Inmuebles24", dynamic_ncols=True)
    try:
        for (muni, ts, tl, os_, ol) in pbar:
            key = (muni, ts, os_)
            pbar.set_description(f"{muni[:12]}/{tl[:6]}/{ol[:5]}")
            if key in done and not args.force:
                pbar.set_postfix(total=total_new, last="skip"); continue
            n = 0
            for page in range(1, max_pages + 1):
                url = page_url(base, ts, os_, muni, page)
                try:
                    html = browser.get_html(url)
                except Exception as e:
                    logger.warning("  ✗ %s p%d: %s", url, page, e); break
                cards = parse_cards(html)
                if not cards:
                    break
                page_new = 0
                for it in cards:
                    if it["id"] in seen:
                        continue
                    seen.add(it["id"])
                    raw_f.write(json.dumps({**it, "_tipo": tl, "_operacion": ol}, ensure_ascii=False) + "\n")
                    writer.writerow(normalize(it, tl, ol, base))
                    n += 1; total_new += 1; page_new += 1
                raw_f.flush(); csv_f.flush()
                pbar.set_postfix(combo=n, total=total_new, pg=page)
                if len(cards) < 20 or page_new == 0:  # última página / sin nuevos
                    break
            done.add(key)
            ck["done"] = [list(k) for k in sorted(done)]
            ck["counts"][f"{muni}/{ts}/{os_}"] = n
            save_ck(ck)
    finally:
        pbar.close(); browser.close(); raw_f.close(); csv_f.close()

    logger.info("=" * 56)
    logger.info("TOTAL postings nuevos: %d", total_new)
    logger.info("Raw: %s | CSV: %s", raw_path, csv_path)
    logger.info("Siguiente: geocodificar por colonia → python -m src.ingesta.geocode --portal inmuebles24")


def main():
    ap = argparse.ArgumentParser(description="CIMENTA · Ingesta Inmuebles24 (CloakBrowser)")
    ap.add_argument("--municipio"); ap.add_argument("--tipo"); ap.add_argument("--operacion")
    ap.add_argument("--max-pages", type=int)
    ap.add_argument("--no-headless", action="store_true", help="muestra el navegador")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        logger.info("Interrumpido. Reanuda con el mismo comando (checkpoint guardado).")
        sys.exit(130)


if __name__ == "__main__":
    main()
