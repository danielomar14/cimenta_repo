"""
CIMENTA · M1 Ingesta · Portal: Lamudi (lamudi.com.mx)

Nivel A — robots.txt permite las páginas de listado (bloquea forms, prefetch
y URLs con search-filter/action/version).

El sitio es server-rendered con JSON-LD. Cada página de resultados trae UN bloque
<script type="application/ld+json"> cuyo @graph[0] es un SearchResultsPage, y los
anuncios están en:  mainEntity[0].itemListElement[*].item
Cada item es schema.org (SingleFamilyResidence/Apartment/…) con:
  name, @type, numberOfBedrooms, numberOfBathroomsTotal, floorSize(value=m²),
  geo(latitude,longitude), address(region,locality,street), offers(price,currency), url

  URL      : /{estado}/{operacion}/?page=N    (operacion = for-sale | for-rent; 30/pág)
  Total    : Lamudi NO expone fecha de publicación → se toman anuncios ACTIVOS.

Estrategia: estado × operación → pagina hasta vaciar (o max_pages) → dedup por url.
Guarda JSONL (raw) + CSV (plano). Reanudable (checkpoint por estado/operación).

Uso:
    python -m src.ingesta.lamudi
    python -m src.ingesta.lamudi --estado distrito-federal --operacion for-sale --max-pages 2
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
RAW_DIR = ROOT / "data" / "raw" / "lamudi"
PROC_DIR = ROOT / "data" / "processed"
CHECKPOINT = RAW_DIR / "_progress.json"
PORTAL = "lamudi"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(PORTAL)

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]
_LD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["ingesta"][PORTAL]


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    ad = HTTPAdapter(max_retries=retry)
    s.mount("https://", ad); s.mount("http://", ad)
    return s


def _to_num(x):
    try:
        return float(str(x).replace(",", ""))
    except (ValueError, TypeError):
        return None


def extract_listings(html: str) -> list[dict]:
    """De la página de resultados → lista de items schema.org."""
    m = _LD_RE.search(html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    node = (data[0] if isinstance(data, list) else data).get("@graph", [{}])[0]
    out = []
    for me in node.get("mainEntity", []) or []:
        for el in me.get("itemListElement", []) or []:
            item = el.get("item")
            if isinstance(item, dict) and ("geo" in item or "offers" in item):
                out.append(item)
    return out


class Lamudi:
    def __init__(self, cfg: dict):
        self.base = cfg["base_url"].rstrip("/")
        self.delay = cfg.get("delay_seg", [1.5, 3.5])
        self.max_pages = int(cfg.get("max_pages", 100))
        self.session = make_session()

    def _sleep(self):
        time.sleep(random.uniform(self.delay[0], self.delay[1]))

    def fetch(self, estado: str, op_slug: str, page: int) -> list[dict]:
        url = f"{self.base}/{estado}/{op_slug}/"
        params = {"page": page} if page > 1 else None
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
            "Referer": f"{self.base}/",
        }
        r = self.session.get(url, params=params, headers=headers, timeout=30)
        if r.status_code != 200:
            return []
        r.encoding = "utf-8"  # Lamudi no declara charset → forzar UTF-8 (evita mojibake)
        return extract_listings(r.text)

    def scrape(self, estado: str, op_slug: str):
        seen_pages = 0
        for page in range(1, self.max_pages + 1):
            try:
                items = self.fetch(estado, op_slug, page)
            except requests.RequestException as e:
                logger.warning("  ✗ %s/%s p%d: %s", estado, op_slug, page, e)
                break
            if not items:
                break
            for it in items:
                yield it
            seen_pages += 1
            if len(items) < 30:   # última página
                break
            self._sleep()


# ── Normalización ────────────────────────────────────────────────────────────────
CSV_FIELDS = [
    "id", "portal", "tipo", "operacion", "estado", "municipio", "neighborhood",
    "name", "currency", "precio", "surface", "rooms", "bathrooms",
    "lat", "lng", "url", "image",
]


def _listing_id(it: dict) -> str:
    url = it.get("url") or it.get("@id") or ""
    return url.rstrip("/").rsplit("/", 1)[-1] if url else ""


def normalize(it: dict, estado: str, operacion: str) -> dict:
    geo = it.get("geo") or {}
    offers = it.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    addr = it.get("address") or {}
    fs = it.get("floorSize") or {}
    return {
        "id": _listing_id(it),
        "portal": PORTAL,
        "tipo": it.get("@type"),
        "operacion": operacion,
        "estado": addr.get("addressRegion") or estado,
        "municipio": addr.get("addressLocality"),
        "neighborhood": addr.get("addressLocality"),
        "name": (it.get("name") or "").strip(),
        "currency": offers.get("priceCurrency"),
        "precio": _to_num(offers.get("price")),
        "surface": _to_num(fs.get("value")),
        "rooms": _to_num(it.get("numberOfBedrooms")),
        "bathrooms": _to_num(it.get("numberOfBathroomsTotal")),
        "lat": _to_num(geo.get("latitude")),
        "lng": _to_num(geo.get("longitude")),
        "url": it.get("url"),
        "image": it.get("image"),
    }


# ── Checkpoint ───────────────────────────────────────────────────────────────────
def load_ck() -> dict:
    return json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else {"done": [], "counts": {}}


def save_ck(ck: dict):
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(ck, indent=2, ensure_ascii=False))


# ── Orquestación ─────────────────────────────────────────────────────────────────
def run(args):
    cfg = load_config()
    estados = [args.estado] if args.estado else cfg["estados"]
    op_map = cfg["operaciones"]  # {slug: etiqueta}
    op_slugs = [args.operacion] if args.operacion else list(op_map.keys())
    if args.max_pages:
        cfg["max_pages"] = args.max_pages

    client = Lamudi(cfg)
    RAW_DIR.mkdir(parents=True, exist_ok=True); PROC_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    raw_path = RAW_DIR / f"listings_{stamp}.jsonl"
    csv_path = PROC_DIR / "lamudi.csv"

    ck = load_ck(); done = set(tuple(x) for x in ck["done"])
    seen_ids: set = set()
    if raw_path.exists():
        with raw_path.open(encoding="utf-8") as f:
            for line in f:
                try: seen_ids.add(_listing_id(json.loads(line)))
                except Exception: pass

    raw_f = raw_path.open("a", encoding="utf-8")
    new_csv = not csv_path.exists()
    csv_f = csv_path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_f, fieldnames=CSV_FIELDS)
    if new_csv: writer.writeheader()

    combos = [(e, s) for e in estados for s in op_slugs]
    total_new = 0
    pbar = tqdm(combos, unit="combo", desc="Lamudi", dynamic_ncols=True)
    try:
        for (estado, op_slug) in pbar:
            pbar.set_description(f"{estado[:16]}/{op_slug}")
            key = (estado, op_slug)
            if key in done and not args.force:
                pbar.set_postfix(total=total_new, last="skip"); continue
            n = 0
            for it in client.scrape(estado, op_slug):
                pid = _listing_id(it)
                if not pid or pid in seen_ids:
                    continue
                seen_ids.add(pid)
                raw_f.write(json.dumps(it, ensure_ascii=False) + "\n")
                writer.writerow(normalize(it, estado, op_map[op_slug]))
                n += 1; total_new += 1
            raw_f.flush(); csv_f.flush()
            done.add(key)
            ck["done"] = [list(k) for k in sorted(done)]
            ck["counts"][f"{estado}/{op_slug}"] = n
            save_ck(ck)
            pbar.set_postfix(combo=n, total=total_new)
    finally:
        pbar.close(); raw_f.close(); csv_f.close()

    logger.info("=" * 56)
    logger.info("TOTAL listados nuevos (activos): %d", total_new)
    logger.info("Raw: %s", raw_path)
    logger.info("CSV: %s", csv_path)


def main():
    ap = argparse.ArgumentParser(description="CIMENTA · Ingesta Lamudi")
    ap.add_argument("--estado")
    ap.add_argument("--operacion", help="for-sale | for-rent")
    ap.add_argument("--max-pages", type=int)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        logger.info("Interrumpido. Reanuda con el mismo comando (checkpoint guardado).")
        sys.exit(130)


if __name__ == "__main__":
    main()
