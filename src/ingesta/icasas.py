"""
CIMENTA · M1 Ingesta · Portal: iCasas (icasas.mx) — Nivel A

HTML server-rendered con microdata schema.org. Cada anuncio es un itemscope
"Residence" con GeoCoordinates (latitud/longitud REALES — no necesita geocode).
El precio viene en texto ("N MX$"); recámaras/baños/m² en el texto de la tarjeta.

  URL: /venta/habitacionales-{tipo}-distrito-federal-{alcaldia}-2_{tc}_1_0_{code}_0[/p_N]
  Paginación: sufijo /p_N. Solo venta (el universo es venta).

Estrategia: alcaldía × tipo → pagina con /p_N → parsea tarjetas (bs4) → dedup por
id (de la URL) → JSONL + CSV. Reanudable.

Uso:
    python -m src.ingesta.icasas
    python -m src.ingesta.icasas --alcaldia benito-juarez --tipo departamentos --max-pages 2
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import re
import sys
import time
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
RAW_DIR = ROOT / "data" / "raw" / "icasas"
PROC_DIR = ROOT / "data" / "processed"
CHECKPOINT = RAW_DIR / "_progress.json"
PORTAL = "icasas"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(PORTAL)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

CSV_FIELDS = ["id", "portal", "tipo", "operacion", "municipio", "colonia", "address",
              "currency", "precio", "surface", "rooms", "bathrooms", "lat", "lng", "url"]


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["ingesta"][PORTAL]


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    ad = HTTPAdapter(max_retries=retry)
    s.mount("https://", ad); s.mount("http://", ad)
    return s


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except (ValueError, TypeError):
        return None


def parse_cards(html: str, base: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for c in soup.select('[itemtype*="Residence"]'):
        a = c.select_one('a[href*="inmueble"]') or c.select_one('a[itemprop="url"]') or c.select_one("a[href]")
        href = a.get("href") if a else None
        if not href:
            continue
        pid = href.split("?")[0].split("#")[0]  # la ruta /propiedad/... es el id único
        lat = c.select_one('[itemprop="latitude"]')
        lng = c.select_one('[itemprop="longitude"]')
        name = c.select_one('[itemprop="name"]')
        addr = c.select_one('[itemprop="streetAddress"]')
        txt = c.get_text(" ", strip=True)
        precio = re.search(r"([\d,]{4,})\s*MX\$", txt)
        rooms = re.search(r"(\d+)\s*rec", txt, re.I)
        baths = re.search(r"(\d+)\s*ba(?:ño|nio|th)", txt, re.I)
        m2 = re.search(r"(\d+)\s*m(?:2|²)", txt)
        out.append({
            "id": pid,
            "url": href if href.startswith("http") else f"{base}{href}",
            "lat": (lat.get("content") if lat else None),
            "lng": (lng.get("content") if lng else None),
            "name": (name.get_text(strip=True) if name else None),
            "address": (addr.get_text(strip=True) if addr else None),
            "precio": _num(precio.group(1)) if precio else None,
            "rooms": _num(rooms.group(1)) if rooms else None,
            "bathrooms": _num(baths.group(1)) if baths else None,
            "surface": _num(m2.group(1)) if m2 else None,
        })
    return out


def normalize(it: dict, tipo: str, alcaldia: str) -> dict:
    addr = it.get("address") or ""
    colonia = addr.split(",")[0].strip() if addr else None
    return {
        "id": it["id"], "portal": PORTAL, "tipo": tipo, "operacion": "venta",
        "municipio": alcaldia.replace("-", " ").title(), "colonia": colonia,
        "address": it.get("address"), "currency": "MXN", "precio": it.get("precio"),
        "surface": it.get("surface"), "rooms": it.get("rooms"), "bathrooms": it.get("bathrooms"),
        "lat": it.get("lat"), "lng": it.get("lng"), "url": it.get("url"),
    }


def page_url(base: str, tipo: str, tc: int, alcaldia: str, code: int, page: int) -> str:
    slug = f"/venta/habitacionales-{tipo}-distrito-federal-{alcaldia}-2_{tc}_1_0_{code}_0"
    return f"{base}{slug}" + (f"/p_{page}" if page > 1 else "")


def load_ck() -> dict:
    return json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else {"done": [], "counts": {}}


def save_ck(ck: dict):
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(ck, indent=2, ensure_ascii=False))


def run(args):
    cfg = load_config()
    base = cfg["base_url"].rstrip("/")
    max_pages = args.max_pages or int(cfg.get("max_pages", 40))
    tipos = {args.tipo: cfg["tipos"][args.tipo]} if args.tipo else cfg["tipos"]
    alcaldias = {args.alcaldia: cfg["alcaldias"][args.alcaldia]} if args.alcaldia else cfg["alcaldias"]
    combos = [(a, ac, t, tc) for a, ac in alcaldias.items() for t, tc in tipos.items()]

    RAW_DIR.mkdir(parents=True, exist_ok=True); PROC_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"listings_{time.strftime('%Y%m%d')}.jsonl"
    csv_path = PROC_DIR / "icasas.csv"
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

    session = make_session()
    total_new = 0
    pbar = tqdm(combos, unit="combo", desc="iCasas", dynamic_ncols=True)
    try:
        for (alcaldia, code, tipo, tc) in pbar:
            key = (alcaldia, tipo)
            pbar.set_description(f"{alcaldia[:12]}/{tipo[:6]}")
            if key in done and not args.force:
                pbar.set_postfix(total=total_new, last="skip"); continue
            n = 0
            for page in range(1, max_pages + 1):
                url = page_url(base, tipo, tc, alcaldia, code, page)
                try:
                    r = session.get(url, headers={"User-Agent": UA, "Accept-Language": "es-MX,es;q=0.9"}, timeout=30)
                    if r.status_code != 200:
                        break
                    r.encoding = "utf-8"
                    cards = parse_cards(r.text, base)
                except requests.RequestException as e:
                    logger.warning("  ✗ %s p%d: %s", url, page, e); break
                if not cards:
                    break
                page_new = 0
                for it in cards:
                    if it["id"] in seen:
                        continue
                    seen.add(it["id"])
                    raw_f.write(json.dumps({**it, "_tipo": tipo, "_alcaldia": alcaldia}, ensure_ascii=False) + "\n")
                    writer.writerow(normalize(it, tipo, alcaldia))
                    n += 1; total_new += 1; page_new += 1
                raw_f.flush(); csv_f.flush()
                pbar.set_postfix(combo=n, total=total_new, pg=page)
                if page_new == 0:
                    break
                time.sleep(random.uniform(1.0, 2.5))
            done.add(key)
            ck["done"] = [list(k) for k in sorted(done)]
            ck["counts"][f"{alcaldia}/{tipo}"] = n
            save_ck(ck)
    finally:
        pbar.close(); raw_f.close(); csv_f.close()

    logger.info("=" * 56)
    logger.info("TOTAL listados nuevos (venta): %d", total_new)
    logger.info("Raw: %s | CSV: %s", raw_path, csv_path)


def main():
    ap = argparse.ArgumentParser(description="CIMENTA · Ingesta iCasas")
    ap.add_argument("--alcaldia"); ap.add_argument("--tipo")
    ap.add_argument("--max-pages", type=int); ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        logger.info("Interrumpido. Reanuda con el mismo comando.")
        sys.exit(130)


if __name__ == "__main__":
    main()
