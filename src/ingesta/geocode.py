"""
CIMENTA · M1 Ingesta · Geocodificación por colonia (OSM / Nominatim)

Para portales que no traen coordenadas (Inmuebles24, etc.): toma las columnas
colonia + municipio de su CSV, geocodifica cada par ÚNICO a un centroide vía
Nominatim (OpenStreetMap), cachea el resultado y rellena lat/lng en el CSV.

Respeta la política de uso de Nominatim:
  · máx 1 request/segundo  · User-Agent identificable  · resultados cacheados.
Sesga los resultados a la ZMVM con un viewbox (bounded) para no caer en
colonias homónimas de otras ciudades.

Uso:
    python -m src.ingesta.geocode --portal inmuebles24
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import time
from pathlib import Path

import requests

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(it, **k):
        return it

ROOT = Path(__file__).resolve().parents[2]
PROC_DIR = ROOT / "data" / "processed"
CACHE_PATH = ROOT / "data" / "geocode_cache.json"

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "CIMENTA-geocoder/1.0 (danielomar.becerrilolguin@gmail.com)"
# viewbox ZMVM: lon_min,lat_max,lon_max,lat_min  (bounded=1)
VIEWBOX = "-99.6,20.1,-98.6,18.9"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("geocode")


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=0))


def geocode_one(session: requests.Session, colonia: str, municipio: str):
    q = f"{colonia}, {municipio}, México"
    params = {
        "q": q, "format": "json", "limit": 1,
        "countrycodes": "mx", "viewbox": VIEWBOX, "bounded": 1,
    }
    try:
        r = session.get(NOMINATIM, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        if data:
            return [float(data[0]["lat"]), float(data[0]["lon"])]
    except (requests.RequestException, ValueError, KeyError):
        pass
    return None


def run(portal: str):
    csv_path = PROC_DIR / f"{portal}.csv"
    if not csv_path.exists():
        logger.error("No existe %s", csv_path); return

    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fields = rows[0].keys() if rows else []
    if "lat" not in fields or "colonia" not in fields:
        logger.error("El CSV necesita columnas 'colonia', 'municipio', 'lat', 'lng'."); return

    cache = load_cache()
    # pares únicos sin coordenadas
    pending = set()
    for r in rows:
        if not r.get("lat") and r.get("colonia") and r.get("municipio"):
            key = f"{r['colonia']}|{r['municipio']}"
            if key not in cache:
                pending.add((r["colonia"], r["municipio"]))

    logger.info("Colonias únicas a geocodificar: %d (cache: %d)", len(pending), len(cache))
    session = requests.Session()
    session.headers["User-Agent"] = UA
    for colonia, municipio in tqdm(sorted(pending), unit="colonia"):
        key = f"{colonia}|{municipio}"
        cache[key] = geocode_one(session, colonia, municipio)
        save_cache(cache)
        time.sleep(1.1)  # política Nominatim: ≤1 req/s

    # rellenar coords
    filled = 0
    for r in rows:
        if not r.get("lat"):
            hit = cache.get(f"{r.get('colonia')}|{r.get('municipio')}")
            if hit:
                r["lat"], r["lng"] = hit[0], hit[1]
                filled += 1

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(fields))
        w.writeheader(); w.writerows(rows)

    geo_ok = sum(1 for r in rows if r.get("lat"))
    logger.info("Filas con coordenadas: %d / %d (rellenadas esta vez: %d)", geo_ok, len(rows), filled)
    logger.info("Caché: %s", CACHE_PATH)


def main():
    ap = argparse.ArgumentParser(description="CIMENTA · Geocodificación por colonia (Nominatim)")
    ap.add_argument("--portal", required=True, help="nombre del CSV en data/processed (sin .csv)")
    args = ap.parse_args()
    run(args.portal)


if __name__ == "__main__":
    main()
