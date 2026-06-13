"""
CIMENTA · M1 Ingesta · Portal: Vivanuncios (Nivel B — vía CloakBrowser)

Vivanuncios usa el MISMO DOM "posting" de Navent que Inmuebles24, así que se
reutiliza su parser (src/ingesta/inmuebles24.parse_cards). Lo único distinto es
el esquema de URL, con códigos de categoría y de ubicación:

  URL: /s-{slug}/{loc-slug}/v1c{cat}l{loc}p{N}     (30/pág)
       p.ej. /s-departamentos-en-venta/distrito-federal/v1c1294l1008p1

No expone coords en la tarjeta → se geocodifica por colonia (src/ingesta/geocode.py).

Uso:
    python -m src.ingesta.vivanuncios
    python -m src.ingesta.vivanuncios --max-pages 2
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path

import yaml

from src.ingesta.inmuebles24 import parse_cards

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
RAW_DIR = ROOT / "data" / "raw" / "vivanuncios"
PROC_DIR = ROOT / "data" / "processed"
CHECKPOINT = RAW_DIR / "_progress.json"
PORTAL = "vivanuncios"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(PORTAL)

CSV_FIELDS = [
    "id", "portal", "tipo", "operacion", "card_type", "municipio", "colonia",
    "address", "currency", "precio", "precio_desde", "surface", "rooms",
    "bathrooms", "parking", "lat", "lng", "url",
]


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["ingesta"][PORTAL]


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
        "url": f"{base_url}{url}" if url and url.startswith("/") else (url or ""),
    }


def load_ck() -> dict:
    return json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else {"done": [], "counts": {}}


def save_ck(ck: dict):
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(ck, indent=2, ensure_ascii=False))


def page_url(base: str, slug: str, loc_slug: str, cat: int, loc: int, page: int) -> str:
    return f"{base}/s-{slug}/{loc_slug}/v1c{cat}l{loc}p{page}"


def run(args):
    from src.ingesta.browser import BrowserSession

    cfg = load_config()
    base = cfg["base_url"].rstrip("/")
    max_pages = args.max_pages or int(cfg.get("max_pages", 50))
    cats = cfg["categorias"]; locs = cfg["locaciones"]
    combos = [(c, ls, lc) for c in cats for ls, lc in locs.items()]

    RAW_DIR.mkdir(parents=True, exist_ok=True); PROC_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    raw_path = RAW_DIR / f"postings_{stamp}.jsonl"
    csv_path = PROC_DIR / "vivanuncios.csv"

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
    pbar = tqdm(combos, unit="combo", desc="Vivanuncios", dynamic_ncols=True)
    try:
        for (cat, loc_slug, loc) in pbar:
            tipo, operacion = cat["tipo"], cat["operacion"]
            key = (cat["slug"], loc_slug)
            pbar.set_description(f"{loc_slug[:10]}/{tipo[:6]}/{operacion[:5]}")
            if key in done and not args.force:
                pbar.set_postfix(total=total_new, last="skip"); continue
            n = 0
            for page in range(1, max_pages + 1):
                url = page_url(base, cat["slug"], loc_slug, cat["cat"], loc, page)
                try:
                    html = browser.get_html(url)
                except Exception as e:
                    logger.warning("  ✗ %s: %s", url, e); break
                cards = parse_cards(html)
                if not cards:
                    break
                page_new = 0
                for it in cards:
                    if it["id"] in seen:
                        continue
                    seen.add(it["id"])
                    raw_f.write(json.dumps({**it, "_tipo": tipo, "_op": operacion}, ensure_ascii=False) + "\n")
                    writer.writerow(normalize(it, tipo, operacion, base))
                    n += 1; total_new += 1; page_new += 1
                raw_f.flush(); csv_f.flush()
                pbar.set_postfix(combo=n, total=total_new, pg=page)
                if len(cards) < 20 or page_new == 0:
                    break
            done.add(key)
            ck["done"] = [list(k) for k in sorted(done)]
            ck["counts"][f"{cat['slug']}/{loc_slug}"] = n
            save_ck(ck)
    finally:
        pbar.close(); browser.close(); raw_f.close(); csv_f.close()

    logger.info("=" * 56)
    logger.info("TOTAL postings nuevos: %d", total_new)
    logger.info("Raw: %s | CSV: %s", raw_path, csv_path)
    logger.info("Siguiente: geocodificar → python -m src.ingesta.geocode --portal vivanuncios")


def main():
    ap = argparse.ArgumentParser(description="CIMENTA · Ingesta Vivanuncios (CloakBrowser)")
    ap.add_argument("--max-pages", type=int)
    ap.add_argument("--no-headless", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        logger.info("Interrumpido. Reanuda con el mismo comando.")
        sys.exit(130)


if __name__ == "__main__":
    main()
