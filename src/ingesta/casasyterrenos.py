"""
CIMENTA · M1 Ingesta · Portal: Casas y Terrenos (casasyterrenos.com)

Nivel A — robots.txt permite los listados para User-Agent: *.
El sitio es Next.js: cada página de búsqueda trae el JSON completo en
<script id="__NEXT_DATA__">. No requiere navegador ni resolver anti-bot.

  URL      : /{estado}/{municipio}/{tipo}/{operacion}?page=N   (240/pág, 1-indexed)
  Datos    : props.pageProps.initialState.propertyData.properties
  Total    : props.pageProps.initialState.propertyData.estimatedTotalHits
  Fecha    : campo lastUpdate (ISO) — última actualización del anuncio.
             (El listado NO expone fecha de publicación pura; usamos lastUpdate
              como proxy de "publicación reciente". Ver --meses.)

Estrategia:
  • Itera estado → municipio → tipo → operación (desde config.yml).
  • Pagina hasta cubrir estimatedTotalHits.
  • Filtra por lastUpdate >= hoy - N meses.
  • Deduplica por id global.
  • Guarda raw (JSONL) + CSV plano. Reanudable (checkpoint por combo).

Uso:
    python -m src.ingesta.casasyterrenos                 # corrida completa
    python -m src.ingesta.casasyterrenos --limit-combos 3   # prueba: 3 combos
    python -m src.ingesta.casasyterrenos --estado ciudad-de-mexico --municipio benito-juarez
    python -m src.ingesta.casasyterrenos --meses 3 --tipo casas --operacion venta
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
from datetime import datetime
from pathlib import Path

import requests
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Rutas del proyecto ─────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config.yml"
RAW_DIR = ROOT / "data" / "raw" / "casasyterrenos"
PROC_DIR = ROOT / "data" / "processed"
CHECKPOINT = RAW_DIR / "_progress.json"

PORTAL = "casasyterrenos"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(PORTAL)

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
]

_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL
)


# ── Utilidades ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg["ingesta"][PORTAL]


def months_ago(n: int) -> datetime:
    """Fecha de corte: hoy menos N meses (aritmética de meses, sin dateutil)."""
    now = datetime.now()
    month = now.month - n
    year = now.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(now.day, 28)  # evita días inexistentes
    return now.replace(year=year, month=month, day=day, hour=0, minute=0, second=0, microsecond=0)


def make_session(retries: int = 3) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=retries, backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def parse_next_data(html: str) -> dict | None:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def parse_last_update(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except ValueError:
        return None


# ── Cliente ──────────────────────────────────────────────────────────────────────

class CasasYTerrenos:
    def __init__(self, cfg: dict, delay=None):
        self.base = cfg["base_url"].rstrip("/")
        self.hits_per_page = int(cfg.get("hits_per_page", 240))
        self.delay = delay or cfg.get("delay_seg", [1.5, 3.5])
        self.session = make_session()

    def _sleep(self):
        time.sleep(random.uniform(self.delay[0], self.delay[1]))

    def fetch_page(self, estado: str, municipio: str, tipo: str, operacion: str, page: int):
        """Devuelve (properties:list, estimated_total:int|None). page es 1-indexed."""
        url = f"{self.base}/{estado}/{municipio}/{tipo}/{operacion}"
        params = {"page": page} if page > 1 else None
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
            "Referer": f"{self.base}/",
        }
        r = self.session.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        data = parse_next_data(r.text)
        if not data:
            return [], None
        pd = data.get("props", {}).get("pageProps", {}).get("initialState", {}).get("propertyData", {})
        return pd.get("properties", []) or [], pd.get("estimatedTotalHits")

    def scrape_combo(self, estado, municipio, tipo, operacion, cutoff: datetime, max_pages=200):
        """Itera páginas de un combo; rinde propiedades dentro de la ventana de fecha."""
        page = 1
        total = None
        collected = 0
        while page <= max_pages:
            try:
                props, est = self.fetch_page(estado, municipio, tipo, operacion, page)
            except requests.RequestException as e:
                logger.warning("    ✗ %s/%s/%s/%s p%d: %s", estado, municipio, tipo, operacion, page, e)
                break
            if total is None:
                total = est or 0
            if not props:
                break
            for p in props:
                lu = parse_last_update(p.get("lastUpdate"))
                if lu is None or lu >= cutoff:
                    yield p
            collected += len(props)
            if total and collected >= total:
                break
            if len(props) < self.hits_per_page:
                break
            page += 1
            self._sleep()


# ── Normalización ────────────────────────────────────────────────────────────────

CSV_FIELDS = [
    "id", "portal", "tipo", "operacion", "estado", "municipio", "neighborhood",
    "name", "isSale", "isRent", "currency", "priceSale", "priceRent",
    "surface", "construction", "rooms", "bathrooms", "parkingLots",
    "lat", "lng", "lastUpdate", "broker", "url",
]


def normalize(p: dict, estado: str, tipo: str, operacion: str, base_url: str) -> dict:
    geo = p.get("_geo") or {}
    broker = p.get("broker") or {}
    broker_name = broker.get("name") if isinstance(broker, dict) else broker
    canonical = p.get("canonical") or (p.get("slugs", {}) or {}).get("canonical", "")
    return {
        "id": p.get("id"),
        "portal": PORTAL,
        "tipo": tipo,
        "operacion": operacion,
        "estado": estado,
        "municipio": p.get("municipality"),
        "neighborhood": p.get("neighborhood"),
        "name": (p.get("name") or "").strip(),
        "isSale": bool(p.get("isSale")),
        "isRent": bool(p.get("isRent")),
        "currency": p.get("currency"),
        "priceSale": p.get("priceSale"),
        "priceRent": p.get("priceRent"),
        "surface": p.get("surface"),
        "construction": p.get("construction"),
        "rooms": p.get("rooms"),
        "bathrooms": p.get("bathrooms"),
        "parkingLots": p.get("parkingLots"),
        "lat": geo.get("lat"),
        "lng": geo.get("lng"),
        "lastUpdate": p.get("lastUpdate"),
        "broker": broker_name,
        "url": f"{base_url}{canonical}" if canonical else "",
    }


# ── Checkpoint ───────────────────────────────────────────────────────────────────

def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text())
    return {"done_combos": [], "counts": {}}


def save_checkpoint(ck: dict) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(ck, indent=2, ensure_ascii=False))


# ── Orquestación ─────────────────────────────────────────────────────────────────

def build_combos(cfg: dict, args) -> list[tuple]:
    combos = []
    zonas = cfg["zonas"]
    estados = [args.estado] if args.estado else list(zonas.keys())
    tipos = [args.tipo] if args.tipo else cfg["tipos"]
    operaciones = [args.operacion] if args.operacion else cfg["operaciones"]
    for estado in estados:
        municipios = [args.municipio] if args.municipio else zonas.get(estado, [])
        for municipio in municipios:
            for tipo in tipos:
                for operacion in operaciones:
                    combos.append((estado, municipio, tipo, operacion))
    return combos


def run(args) -> None:
    cfg = load_config()
    meses = args.meses if args.meses is not None else int(cfg.get("meses_atras", 3))
    cutoff = months_ago(meses)
    logger.info("Corte por lastUpdate: >= %s (%d meses)", cutoff.date(), meses)

    client = CasasYTerrenos(cfg)
    combos = build_combos(cfg, args)
    if args.limit_combos:
        combos = combos[: args.limit_combos]
    logger.info("Combos a recorrer: %d", len(combos))

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    raw_path = RAW_DIR / f"properties_{stamp}.jsonl"
    csv_path = PROC_DIR / "casasyterrenos.csv"

    ck = load_checkpoint()
    done = set(tuple(c) for c in ck["done_combos"])
    seen_ids: set = set()
    # precarga de IDs ya guardados (para dedup entre corridas)
    if raw_path.exists():
        with raw_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    seen_ids.add(json.loads(line)["id"])
                except Exception:
                    pass

    raw_f = raw_path.open("a", encoding="utf-8")
    new_csv = not csv_path.exists()
    csv_f = csv_path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_f, fieldnames=CSV_FIELDS)
    if new_csv:
        writer.writeheader()

    total_new = 0
    try:
        for i, (estado, municipio, tipo, operacion) in enumerate(combos, 1):
            key = (estado, municipio, tipo, operacion)
            if key in done and not args.force:
                continue
            n_combo = 0
            for p in client.scrape_combo(estado, municipio, tipo, operacion, cutoff):
                pid = p.get("id")
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                raw_f.write(json.dumps(p, ensure_ascii=False) + "\n")
                writer.writerow(normalize(p, estado, tipo, operacion, client.base))
                n_combo += 1
                total_new += 1
            raw_f.flush(); csv_f.flush()
            done.add(key)
            ck["done_combos"] = [list(k) for k in sorted(done)]
            ck["counts"][f"{estado}/{municipio}/{tipo}/{operacion}"] = n_combo
            save_checkpoint(ck)
            logger.info("[%d/%d] %s/%s/%s/%s → %d nuevos (acum %d)",
                        i, len(combos), estado, municipio, tipo, operacion, n_combo, total_new)
            client._sleep()
    finally:
        raw_f.close(); csv_f.close()

    logger.info("=" * 60)
    logger.info("TOTAL propiedades nuevas (≤%d meses): %d", meses, total_new)
    logger.info("Raw : %s", raw_path)
    logger.info("CSV : %s", csv_path)


def main():
    ap = argparse.ArgumentParser(description="CIMENTA · Ingesta Casas y Terrenos")
    ap.add_argument("--meses", type=int, default=None, help="Ventana de lastUpdate (def: config)")
    ap.add_argument("--estado", help="Solo este estado (slug)")
    ap.add_argument("--municipio", help="Solo este municipio (slug)")
    ap.add_argument("--tipo", help="Solo este tipo")
    ap.add_argument("--operacion", help="venta | renta")
    ap.add_argument("--limit-combos", type=int, help="Máximo de combos (para pruebas)")
    ap.add_argument("--force", action="store_true", help="Reprocesa combos ya marcados como done")
    args = ap.parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        logger.info("Interrumpido. Reanuda con el mismo comando (checkpoint guardado).")
        sys.exit(130)


if __name__ == "__main__":
    main()
