"""
CIMENTA · M1 Ingesta · Portal: Propiedades.com (Nivel B — vía CloakBrowser)

Tras Cloudflare, pero es Next.js: el HTML renderizado trae __NEXT_DATA__ con los
listados YA estructurados y CON coordenadas:
  props.pageProps.results.properties[*] →
    id, url_property, latitude, longitude, valid_coords, colony, municipality, city,
    zipcode, bedrooms, bathrooms, size_m2, parking_num, sale_price_real,
    rental_price_real, currency, property_type, purpose_str, published, days_ago

  URL: /{estado}/{tipo}-{operacion}[?pagina=N]   (47/pág; total en totalItems)

Estrategia: CloakBrowser resuelve Cloudflare una vez → por combo (estado×tipo×op)
pagina, filtra por días desde publicación (≤ N meses), dedup por id, JSONL + CSV.

Uso:
    python -m src.ingesta.propiedades
    python -m src.ingesta.propiedades --estado df --tipo departamentos --operacion venta --max-pages 2
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

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
RAW_DIR = ROOT / "data" / "raw" / "propiedades"
PROC_DIR = ROOT / "data" / "processed"
CHECKPOINT = RAW_DIR / "_progress.json"
PORTAL = "propiedades"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(PORTAL)

_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["ingesta"][PORTAL]


def extract_properties(html: str) -> tuple[list[dict], int | None]:
    m = _NEXT_RE.search(html)
    if not m:
        return [], None
    try:
        d = json.loads(m.group(1))
    except json.JSONDecodeError:
        return [], None
    pp = d.get("props", {}).get("pageProps", {})
    res = pp.get("results", {}) or {}
    return res.get("properties", []) or [], pp.get("totalItems")


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except (ValueError, TypeError):
        return None


def months_ago(n: int) -> datetime:
    now = datetime.now()
    month, year = now.month - n, now.year
    while month <= 0:
        month += 12; year -= 1
    return now.replace(year=year, month=month, day=min(now.day, 28),
                       hour=0, minute=0, second=0, microsecond=0)


def within(published, cutoff) -> bool:
    """True si `published` (campo del anuncio) es >= cutoff. Lenient si no parsea."""
    if not published or not cutoff:
        return True
    try:
        dt = datetime.fromisoformat(str(published).replace("Z", ""))
        return dt.replace(tzinfo=None) >= cutoff
    except ValueError:
        return True


CSV_FIELDS = [
    "id", "portal", "tipo", "operacion", "municipio", "colonia", "zipcode",
    "currency", "precio", "size_m2", "rooms", "bathrooms", "parking",
    "lat", "lng", "valid_coords", "days_ago", "published", "url",
]


def normalize(p: dict, tipo: str, operacion: str) -> dict:
    sale = _num(p.get("sale_price_real")); rent = _num(p.get("rental_price_real"))
    precio = sale if operacion == "venta" else rent
    if not precio:
        precio = _num(p.get("price_real")) or _num(p.get("price_num"))
    return {
        "id": p.get("id"), "portal": PORTAL, "tipo": tipo, "operacion": operacion,
        "municipio": p.get("municipality") or p.get("city"), "colonia": p.get("colony"),
        "zipcode": p.get("zipcode"), "currency": p.get("currency"), "precio": precio,
        "size_m2": _num(p.get("size_m2")), "rooms": p.get("bedrooms"),
        "bathrooms": p.get("bathrooms"), "parking": p.get("parking_num"),
        "lat": p.get("latitude"), "lng": p.get("longitude"),
        "valid_coords": p.get("valid_coords"), "days_ago": p.get("days_ago"),
        "published": p.get("published"), "url": p.get("url_property"),
    }


def load_ck() -> dict:
    return json.loads(CHECKPOINT.read_text()) if CHECKPOINT.exists() else {"done": [], "counts": {}}


def save_ck(ck: dict):
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(ck, indent=2, ensure_ascii=False))


def page_url(base: str, estado: str, tipo: str, op_slug: str, page: int) -> str:
    suffix = f"?pagina={page}" if page > 1 else ""
    return f"{base}/{estado}/{tipo}-{op_slug}{suffix}"


def run(args):
    from src.ingesta.browser import BrowserSession

    cfg = load_config()
    base = cfg["base_url"].rstrip("/")
    max_pages = args.max_pages or int(cfg.get("max_pages", 50))
    meses = args.meses if args.meses is not None else int(cfg.get("meses_atras", 3))
    cutoff = months_ago(meses)
    logger.info("Corte por 'published': >= %s (%d meses)", cutoff.date(), meses)
    estados = [args.estado] if args.estado else cfg["estados"]
    tipos = [args.tipo] if args.tipo else cfg["tipos"]
    ops = cfg["operaciones"]
    sel_ops = {args.operacion: ops.get(args.operacion, args.operacion)} if args.operacion else ops

    RAW_DIR.mkdir(parents=True, exist_ok=True); PROC_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    raw_path = RAW_DIR / f"properties_{stamp}.jsonl"
    csv_path = PROC_DIR / "propiedades.csv"

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

    combos = [(e, t, os_, ol) for e in estados for t in tipos for os_, ol in sel_ops.items()]
    total_new = 0
    browser = BrowserSession(warmup_url=cfg["warmup_url"], headless=not args.no_headless,
                             settle=float(cfg.get("settle", 6.0)))
    pbar = tqdm(combos, unit="combo", desc="Propiedades", dynamic_ncols=True)
    try:
        for (estado, tipo, os_, ol) in pbar:
            key = (estado, tipo, os_)
            pbar.set_description(f"{estado[:8]}/{tipo[:6]}/{ol[:5]}")
            if key in done and not args.force:
                pbar.set_postfix(total=total_new, last="skip"); continue
            n = 0
            for page in range(1, max_pages + 1):
                url = page_url(base, estado, tipo, os_, page)
                try:
                    html = browser.get_html(url)
                except Exception as e:
                    logger.warning("  ✗ %s: %s", url, e); break
                props, total = extract_properties(html)
                if not props:
                    break
                page_new = 0
                for p in props:
                    pid = p.get("id")
                    if pid in seen:
                        continue
                    if not within(p.get("published"), cutoff):
                        continue  # fuera de la ventana de N meses
                    seen.add(pid)
                    raw_f.write(json.dumps({**p, "_tipo": tipo, "_op": ol}, ensure_ascii=False) + "\n")
                    writer.writerow(normalize(p, tipo, ol))
                    n += 1; total_new += 1; page_new += 1
                raw_f.flush(); csv_f.flush()
                pbar.set_postfix(combo=n, total=total_new, pg=page)
                if len(props) < 40 or page_new == 0:
                    break
            done.add(key)
            ck["done"] = [list(k) for k in sorted(done)]
            ck["counts"][f"{estado}/{tipo}/{os_}"] = n
            save_ck(ck)
    finally:
        pbar.close(); browser.close(); raw_f.close(); csv_f.close()

    logger.info("=" * 56)
    logger.info("TOTAL propiedades nuevas: %d", total_new)
    logger.info("Raw: %s | CSV: %s", raw_path, csv_path)


def main():
    ap = argparse.ArgumentParser(description="CIMENTA · Ingesta Propiedades.com (CloakBrowser)")
    ap.add_argument("--estado"); ap.add_argument("--tipo"); ap.add_argument("--operacion")
    ap.add_argument("--meses", type=int, default=None)
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
